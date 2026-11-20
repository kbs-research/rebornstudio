"""스케일 아웃 골격 테스트. 판정, 빌드 경쟁, 회수를 확인한다.

펜싱과 완료 판정은 test_transcode_service의 잡 프로토콜 테스트가 다룬다.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.media import transcode
from httpx import AsyncClient
from sqlalchemy import func, select, update
from tests._helpers import new_project, reset_transcode_active, upload_video


@pytest_asyncio.fixture(autouse=True)
async def _isolate_transcode_rows():
    """변환 활성 행을 격리한다. 근거는 `_helpers.reset_transcode_active`의 docstring에 있다."""
    await reset_transcode_active()
    yield


# ── 판정 ────────────────────────────────────────────────────────────────

def test_needs_transcode_codec_beats_resolution():
    """720p av1은 코덱 사유, 720p h264는 재생본 사유, 1080p h264는 축소 사유다."""
    assert transcode.needs_transcode({"video_codec": "av1", "height": 720}) == "codec:av1"
    # 분석본은 필요 없어도 480p 재생본은 만들어야 한다.
    assert transcode.needs_transcode({"video_codec": "h264", "height": 720}) == "playback:720"
    assert transcode.needs_transcode({"video_codec": "h264", "height": 1080}) == "height:1080"
    assert transcode.needs_transcode({"video_codec": "av1", "height": 1080}) == (
        "codec:av1,height:1080"
    )


def test_needs_transcode_unknown_is_not_target():
    """probe 값이 없으면 변환 대상으로 판정하지 않는다. probe 실패가 CPU 낭비로 이어지지 않게 한다."""
    assert transcode.needs_transcode({}) is None
    assert transcode.needs_transcode({"video_codec": None, "height": None}) is None


def test_needs_transcode_profile_overrides():
    """profile이 never면 변환하지 않고 always면 항상 변환한다. 자동 판정보다 사람이 정한 profile이 우선한다."""
    meta = {"video_codec": "av1", "height": 1080}
    assert transcode.needs_transcode(meta, profile="never") is None
    assert transcode.needs_transcode({"video_codec": "h264"}, profile="always") == (
        "profile:always"
    )


# ── 골격: 상한·경쟁·회수 ────────────────────────────────────────────────────

async def _pending_video(client: AsyncClient, admin_token: str) -> uuid.UUID:
    """probe 결과가 av1 1080p이고 변환이 pending인 영상을 만든다."""
    pid = await new_project(client, admin_token, "tsk")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": {"video_codec": "av1", "height": 1080}}
        v.transcode_status = "pending"
        await s.commit()
    return vid


async def _status(vid: uuid.UUID) -> str:
    """영상의 현재 transcode_status를 읽는다."""
    async with SessionLocal() as s:
        return await s.scalar(select(Video.transcode_status).where(Video.video_id == vid))


async def test_build_respects_global_inflight_cap(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """상한이 2이고 running이 1이면 pending 2건 중 1건만 잡이 된다."""
    from api_gateway.config import settings
    monkeypatch.setattr(settings, "transcode_max_inflight", 2)

    v1 = await _pending_video(client, admin_token)
    v2 = await _pending_video(client, admin_token)
    v3 = await _pending_video(client, admin_token)
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == v3)
                        .values(transcode_status="running"))
        await s.commit()

    async with SessionLocal() as s:
        jobs = await transcode.build_jobs(s)
    assert len(jobs) == 1, "상한 2 − running 1 = 1건"
    assert jobs[0]["video_id"] in (str(v1), str(v2))

    async with SessionLocal() as s:
        assert await transcode.build_jobs(s) == [], "상한이 차면 0건"


async def _build_twice_concurrently() -> list[str]:
    """새 세션 두 개로 `build_jobs`를 동시에 돌려 발급된 잡의 영상 id를 모두 모은다."""
    import asyncio

    async def _build() -> list[dict]:
        """build_jobs를 새 세션으로 한 번 돌린다."""
        async with SessionLocal() as s:
            return await transcode.build_jobs(s)

    a, b = await asyncio.gather(_build(), _build())
    return [j["video_id"] for j in a + b]


async def test_concurrent_builders_split_not_duplicate(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """빌더 2개가 동시에 돌아도 같은 영상이 두 잡이 되지 않고 상한 안의 대기 영상은 모두 잡이 된다.

    상한을 대기 영상 수와 같게 둔다. 상한이 더 작으면 남는 영상이 생기는 것이 정상이라
    누락 여부를 판정할 수 없다.
    """
    from api_gateway.config import settings

    vids = {str(await _pending_video(client, admin_token)) for _ in range(4)}
    monkeypatch.setattr(settings, "transcode_max_inflight", len(vids))

    built = [v for v in await _build_twice_concurrently() if v in vids]
    assert len(built) == len(set(built)), "중복 잡 발급"
    assert set(built) == vids, "누락 발생"


async def test_concurrent_builders_respect_inflight_cap(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """빌더 2개가 동시에 돌아도 전역 상한을 넘겨 발급하지 않는다.

    두 빌더가 서로의 커밋 전에 진행 중 건수를 세면 둘 다 상한만큼 골라 상한의 두 배가 발급된다.
    """
    from api_gateway.config import settings
    monkeypatch.setattr(settings, "transcode_max_inflight", 2)

    vids = {str(await _pending_video(client, admin_token)) for _ in range(4)}

    built = await _build_twice_concurrently()
    assert len(built) == len(set(built)), "중복 잡 발급"
    assert len(built) == 2, f"상한 2인데 {len(built)}건 발급"
    async with SessionLocal() as s:
        inflight = await s.scalar(
            select(func.count()).select_from(Video)
            .where(Video.transcode_status.in_(("queued", "running"))))
    assert inflight == 2
    assert set(built) <= vids


async def test_reclaim_recovers_expired_lease(client: AsyncClient, admin_token: str):
    """리스가 만료되면(워커 비정상 종료) pending으로 되돌린다. 잡을 잃지 않고 처리만 늦어진다."""
    vid = await _pending_video(client, admin_token)
    async with SessionLocal() as s:
        await transcode.build_jobs(s)
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == vid).values(
            transcode_lease_until=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
        assert await transcode.reclaim_expired(s) >= 1
    assert await _status(vid) == "pending"
