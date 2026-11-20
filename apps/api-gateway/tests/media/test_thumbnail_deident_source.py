"""썸네일·대표 이미지의 원천 테스트. 비식별화를 쓰는 영상은 비식별화본에서만 뽑는다.

썸네일은 라벨러도 목록에서 본다. 원본에서 뽑으면 가리지 않은 얼굴이 나간다. 네 가지를 확인한다.

  1. 비식별화를 쓰는 영상은 비식별화와 재생본 확정 전에는 뽑지 않는다. 실패로 세지도 않는다.
  2. 확정되면 분석본(비식별화본)에서 뽑고 출처를 남긴다.
  3. 비식별화를 쓰지 않는 영상은 원본에서 뽑는다.
  4. 이미 원본에서 뽑은 썸네일은 키를 지워 다시 뽑게 한다. 비식별화본에서 뽑은 썸네일은 그대로 둔다.

ffmpeg와 저장소는 대역으로 바꾼다.
"""
from decimal import Decimal
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Clip, Project, Video
from api_gateway.services.media import clip_thumbnail, storage, video_thumbnail
from httpx import AsyncClient
from tests._helpers import new_project, upload_video

_SOURCE = "s3://example-bucket/news/raw.mp4"
_MASKED = "s3://example-bucket/transcoded/720p/masked.mp4"


async def _video(
    client: AsyncClient, token: str, name: str, *, deident: bool, status: str,
    published: bool = False, extra: dict | None = None,
) -> tuple[UUID, UUID]:
    """비식별화 설정과 상태를 정한 영상과 썸네일 없는 클립 1건을 만든다. (영상, 클립)을 돌려준다."""
    pid = await new_project(client, token, name)
    vid = UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        (await s.get(Project, UUID(pid))).deident_enabled = deident
        v = await s.get(Video, vid)
        v.source_uri = _SOURCE
        v.deident_status = status
        v.publish_status = "done" if published else "skipped"
        v.analysis_uri = _MASKED if published else None
        v.extra = {**(v.extra or {}), **(extra or {})}
        clip = Clip(
            video_id=vid, project_id=UUID(pid),
            start_seconds=Decimal("10.000"), end_seconds=Decimal("14.500"),
            rights_status="auto_cleared", auto_caption={"brief": "장면"}, speakers=[],
        )
        s.add(clip)
        await s.commit()
        return vid, clip.clip_id


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


async def _clip_candidate(clip_id: UUID) -> bool:
    """이 클립이 다음 주기의 썸네일 후보이면 True. 상한·정렬 없이 조건만 적용한다."""
    async with SessionLocal() as s:
        return (await s.execute(
            clip_thumbnail.pending_select().where(Clip.clip_id == clip_id))).first() is not None


async def _poster_candidate(video_id: UUID) -> bool:
    """이 영상이 다음 주기의 포스터 후보이면 True. 상한·정렬 없이 조건만 적용한다."""
    async with SessionLocal() as s:
        return (await s.execute(
            video_thumbnail.pending_select().where(Video.video_id == video_id))).first() is not None


async def test_masked_video_waits_until_published(
    client: AsyncClient, admin_token: str, read_from: list[str],
):
    """비식별화를 쓰는 영상은 확정 전에는 뽑지 않는다. 대기 상태라 실패로 기록하지 않는다."""
    vid, cid = await _video(client, admin_token, "thumb-deid-wait", deident=True, status="pending")
    assert not await _clip_candidate(cid)
    assert not await _poster_candidate(vid)
    async with SessionLocal() as s:
        assert await clip_thumbnail.process_one_by_id(s, cid) is False
    async with SessionLocal() as s:
        assert await video_thumbnail.process_one_by_id(s, vid) is False
    assert read_from == [], f"비식별화 전에 원본을 읽었다: {read_from}"
    async with SessionLocal() as s:
        c = await s.get(Clip, cid)
        assert "thumbnail_retry_count" not in c.extra and "thumbnail_error" not in c.extra
        v = await s.get(Video, vid)
        assert "poster_retry_count" not in v.extra and "poster_error" not in v.extra

    # 비식별화가 끝나도 재생본 확정 전이면 기다린다. 분석본 위치에 아직 이전 파일이 있을 수 있다.
    async with SessionLocal() as s:
        (await s.get(Video, vid)).deident_status = "done"
        await s.commit()
    assert not await _clip_candidate(cid)


async def test_masked_video_reads_the_masked_analysis_copy(
    client: AsyncClient, admin_token: str, read_from: list[str],
):
    """확정되면 분석본(비식별화본)에서 뽑고 출처를 `deident`로 남긴다."""
    vid, cid = await _video(client, admin_token, "thumb-deid-done", deident=True,
                            status="done", published=True)
    assert await _clip_candidate(cid)
    assert await _poster_candidate(vid)
    async with SessionLocal() as s:
        assert await clip_thumbnail.process_one_by_id(s, cid) is True
    async with SessionLocal() as s:
        assert await video_thumbnail.process_one_by_id(s, vid) is True
    assert read_from == ["transcoded/720p/masked.mp4"] * 2, read_from
    async with SessionLocal() as s:
        assert (await s.get(Clip, cid)).extra["scene_thumbnail_from"] == "deident"
        assert (await s.get(Video, vid)).extra["poster_thumbnail_from"] == "deident"


async def test_unmasked_project_still_reads_the_source(
    client: AsyncClient, admin_token: str, read_from: list[str],
):
    """비식별화를 쓰지 않는 영상은 원본에서 바로 뽑는다."""
    vid, cid = await _video(client, admin_token, "thumb-deid-off", deident=False, status="skipped")
    assert await _clip_candidate(cid)
    async with SessionLocal() as s:
        assert await clip_thumbnail.process_one_by_id(s, cid) is True
    assert read_from == ["news/raw.mp4"]
    async with SessionLocal() as s:
        assert (await s.get(Clip, cid)).extra["scene_thumbnail_from"] == "source"


async def test_thumbnails_cut_from_the_source_are_made_again(
    client: AsyncClient, admin_token: str, read_from: list[str],
):
    """비식별화를 쓰는 영상의 출처 기록 없는 썸네일은 키를 지워 다시 뽑는다. 다시 뽑은 썸네일은 다음 정리에서 지우지 않는다."""
    old = {"poster_thumbnail_key": "video-posters/x.jpg"}
    on_vid, on_cid = await _video(client, admin_token, "thumb-redo-on", deident=True,
                                  status="done", published=True, extra=old)
    off_vid, off_cid = await _video(client, admin_token, "thumb-redo-off", deident=False,
                                    status="skipped", extra=old)
    async with SessionLocal() as s:
        for cid in (on_cid, off_cid):
            c = await s.get(Clip, cid)
            c.extra = {**(c.extra or {}), "scene_thumbnail_key": f"clip-thumbs/{cid}.jpg"}
        await s.commit()

    async with SessionLocal() as s:
        assert await clip_thumbnail.invalidate_unmasked(s) >= 1
        assert await video_thumbnail.invalidate_unmasked(s) >= 1
        await s.commit()
    async with SessionLocal() as s:
        assert "scene_thumbnail_key" not in (await s.get(Clip, on_cid)).extra
        assert "poster_thumbnail_key" not in (await s.get(Video, on_vid)).extra
        assert "scene_thumbnail_key" in (await s.get(Clip, off_cid)).extra, "끈 프로젝트는 그대로"
        assert "poster_thumbnail_key" in (await s.get(Video, off_vid)).extra
    assert await _clip_candidate(on_cid), "지운 뒤 다음 주기 후보가 돼야 한다"

    # 비식별화본에서 다시 뽑으면 다음 정리가 건드리지 않는다.
    async with SessionLocal() as s:
        assert await clip_thumbnail.process_one_by_id(s, on_cid) is True
    async with SessionLocal() as s:
        await clip_thumbnail.invalidate_unmasked(s)
        await s.commit()
    async with SessionLocal() as s:
        assert (await s.get(Clip, on_cid)).extra.get("scene_thumbnail_key"), "다시 지웠다"
