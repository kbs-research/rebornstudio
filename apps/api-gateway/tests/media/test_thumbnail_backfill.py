"""수동 일괄 썸네일(`clip_thumbnail.backfill`, 운영 스크립트가 호출한다) 테스트.

일괄 생성은 정기 작업과 같은 규칙을 따라야 한다. 네 가지를 확인한다.

  1. 비식별화를 쓰는 영상은 비식별화·재생본 확정 전에는 만들지 않는다(원본에서 뽑지 않는다).
  2. 확정되면 분석본(비식별화본)에서 뽑고 출처(`scene_thumbnail_from`)를 남긴다.
  3. 비식별화를 쓰지 않는 영상은 원본에서 뽑고 출처를 남긴다. `--force`가 아니면 이미 있는 썸네일은 그대로 둔다.
  4. 정기 작업이 락을 점유하고 있으면 시작하지 않는다.

범위는 이 테스트의 프로젝트로 좁힌다(`project_id`). 같은 DB에 있는 다른 테스트의 행을 건드리지 않는다.
"""
from decimal import Decimal
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Clip, Project, Video
from api_gateway.services.joblock import single_runner
from api_gateway.services.media import clip_thumbnail, storage
from httpx import AsyncClient
from tests._helpers import new_project, upload_video

_SOURCE = "s3://example-bucket/news/raw.mp4"
_MASKED = "s3://example-bucket/transcoded/720p/masked.mp4"


async def _project(
    client: AsyncClient, token: str, name: str, *, deident: bool, status: str, published: bool,
) -> tuple[UUID, UUID]:
    """비식별화 설정·상태를 정한 영상 하나와 썸네일 없는 클립 하나의 프로젝트를 만든다."""
    pid = await new_project(client, token, name)
    vid = UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        (await s.get(Project, UUID(pid))).deident_enabled = deident
        v = await s.get(Video, vid)
        v.source_uri = _SOURCE
        v.deident_status = status
        v.publish_status = "done" if published else "skipped"
        v.analysis_uri = _MASKED if published else None
        clip = Clip(
            video_id=vid, project_id=UUID(pid),
            start_seconds=Decimal("10.000"), end_seconds=Decimal("14.500"),
            rights_status="auto_cleared", auto_caption={"brief": "장면"}, speakers=[],
        )
        s.add(clip)
        await s.commit()
        return UUID(pid), clip.clip_id


@pytest.fixture
def read_from(monkeypatch) -> list[str]:
    """presign·추출·업로드를 대역으로 바꾸고, 프레임을 뽑으려고 서명한 키 목록을 돌려준다."""
    seen: list[str] = []

    def _presign(**kw):
        """서명한 키를 기록하고 가짜 주소를 돌려준다."""
        seen.append(kw["key"])
        return f"https://ncp/{kw['key']}?sig=1"

    real_put = storage.put_bytes

    def _put(key, *a, **kw):
        """썸네일 업로드만 버린다. 등록 전 저장소 점검의 점검 객체는 실제로 올린다."""
        return None if key.startswith(("clip-thumbs/", "video-posters/")) else real_put(key, *a, **kw)

    monkeypatch.setattr(storage, "presign_get_for", _presign)
    monkeypatch.setattr(storage, "put_bytes", _put)
    monkeypatch.setattr(clip_thumbnail, "_extract_frame", lambda url, ts: b"\xff\xd8jpeg")
    return seen


async def _extra(clip_id: UUID) -> dict:
    """클립의 `extra`를 읽는다."""
    async with SessionLocal() as s:
        return dict((await s.get(Clip, clip_id)).extra or {})


async def test_masked_video_waits_then_reads_the_masked_copy(
    client: AsyncClient, admin_token: str, read_from: list[str],
):
    """비식별화를 쓰는 영상은 확정 전에는 만들지 않고, 확정 뒤에는 비식별화본에서 뽑는다."""
    pid, cid = await _project(client, admin_token, "backfill-masked", deident=True,
                              status="running", published=False)
    async with SessionLocal() as s:
        assert await clip_thumbnail.backfill(s, force=True, project_id=pid) == (0, 1)
    assert read_from == [], "비식별화 전인데 원본에서 뽑았다"
    assert "scene_thumbnail_key" not in await _extra(cid)

    async with SessionLocal() as s:
        await s.execute(Video.__table__.update().where(Video.project_id == pid)
                        .values(deident_status="done", publish_status="done",
                                analysis_uri=_MASKED))
        await s.commit()
    async with SessionLocal() as s:
        assert await clip_thumbnail.backfill(s, project_id=pid) == (1, 0)
    assert read_from == ["transcoded/720p/masked.mp4"]
    extra = await _extra(cid)
    assert extra["scene_thumbnail_key"] == f"clip-thumbs/{cid}.jpg"
    assert extra["scene_thumbnail_from"] == "deident"


async def test_plain_video_reads_the_source_and_records_it(
    client: AsyncClient, admin_token: str, read_from: list[str],
):
    """비식별화를 쓰지 않는 영상은 원본에서 뽑고 출처를 남긴다. 다시 돌려도 이미 있는 썸네일은 그대로 둔다."""
    pid, cid = await _project(client, admin_token, "backfill-plain", deident=False,
                              status="skipped", published=False)
    async with SessionLocal() as s:
        assert await clip_thumbnail.backfill(s, project_id=pid) == (1, 0)
    assert read_from == ["news/raw.mp4"]
    assert (await _extra(cid))["scene_thumbnail_from"] == "source"
    async with SessionLocal() as s:
        assert await clip_thumbnail.backfill(s, project_id=pid) == (0, 0)
    async with SessionLocal() as s:
        assert await clip_thumbnail.backfill(s, force=True, project_id=pid) == (1, 0)


async def test_backfill_steps_aside_while_the_beat_runs(
    client: AsyncClient, admin_token: str, read_from: list[str],
):
    """정기 작업이 락을 점유하고 있으면 시작하지 않고 `BusyError`를 낸다. 같은 클립을 두 번 뽑지 않게 한다."""
    pid, _ = await _project(client, admin_token, "backfill-busy", deident=False,
                            status="skipped", published=False)
    async with single_runner(clip_thumbnail.LOCK_KEY) as got:
        assert got
        async with SessionLocal() as s:
            with pytest.raises(clip_thumbnail.BusyError):
                await clip_thumbnail.backfill(s, project_id=pid)
    assert read_from == []
