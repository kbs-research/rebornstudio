"""시작한 뒤 회수된 시도의 횟수 상한 테스트.

워커 자식 프로세스를 비정상 종료시키는 입력(메모리 초과·디코더 충돌)은 실패 보고 없이 끝난다.
회수가 그 시도를 세지 않으면 같은 입력이 리스가 끝날 때마다 다시 투입되어 끝없이 돈다.
시작하지 못한 시도(처리 서버가 잡을 가져가지 못함)는 영상 탓이 아니므로 세지 않는다.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Project, Video
from api_gateway.services.media import deident as deident_service
from api_gateway.services.media import publish as publish_service
from api_gateway.services.media import transcode as transcode_service
from api_gateway.services.media.pipeline import open_next
from httpx import AsyncClient
from sqlalchemy import update
from tests._helpers import new_project, upload_video


@pytest.fixture(autouse=True)
async def _isolate():
    """앞 테스트가 남긴 변환·publish·비식별화 활성 행을 비운다."""
    from tests._helpers import reset_transcode_active
    await reset_transcode_active()
    yield


async def _expire(vid: uuid.UUID, stage: str) -> None:
    """그 단계의 리스를 지난 시각으로 돌린다. 워커 자식 프로세스가 비정상 종료되어 하트비트가 끊긴 상태와 같다."""
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == vid).values(
            **{f"{stage}_lease_until": datetime.now(UTC) - timedelta(seconds=1)}))
        await s.commit()


async def _row(vid: uuid.UUID) -> Video:
    """영상 행을 새로 읽는다."""
    async with SessionLocal() as s:
        return await s.get(Video, vid)


async def test_transcode_crash_loop_stops_at_the_attempt_cap(
    client: AsyncClient, admin_token: str,
):
    """시작 하트비트 뒤 회수가 상한만큼 이어지면 변환을 실패로 확정한다."""
    pid = await new_project(client, admin_token, "cap-tx")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": {"video_codec": "av1", "height": 1080}}
        v.transcode_status = "pending"
        await s.commit()

    # 시작하지 못한 시도는 세지 않는다.
    async with SessionLocal() as s:
        await transcode_service.build_jobs(s)
    await _expire(vid, "transcode")
    async with SessionLocal() as s:
        await transcode_service.reclaim_expired(s)
    v = await _row(vid)
    assert (v.transcode_status, v.transcode_attempts) == ("pending", 0)

    for n in range(1, transcode_service._MAX_RETRIES + 1):
        async with SessionLocal() as s:
            job = next(j for j in await transcode_service.build_jobs(s)
                       if j["video_id"] == str(vid))
        async with SessionLocal() as s:
            assert await transcode_service.extend_lease(s, vid, job["token"], done_seconds=0.0)
        await _expire(vid, "transcode")
        async with SessionLocal() as s:
            await transcode_service.reclaim_expired(s)
        v = await _row(vid)
        if n < transcode_service._MAX_RETRIES:
            assert (v.transcode_status, v.transcode_attempts) == ("pending", n)
    assert v.transcode_status == "failed"
    assert "3번 멈춰" in v.extra["transcode_error"]


async def test_publish_crash_loop_stops_at_the_attempt_cap(
    client: AsyncClient, admin_token: str,
):
    """재생본도 시작한 뒤 회수된 시도를 세고 상한에서 실패로 확정한다."""
    pid = await new_project(client, admin_token, "cap-pub")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.publish_status = "running"
        v.publish_token = "t-crash"
        v.publish_attempts = publish_service._MAX_RETRIES
        v.publish_done_seconds = 12.0
        v.publish_lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
    async with SessionLocal() as s:
        assert await publish_service.reclaim_expired(s) >= 1
    v = await _row(vid)
    assert v.publish_status == "failed" and "멈춰" in v.extra["publish_error"]


async def test_deident_crash_loop_stops_at_the_attempt_cap(
    client: AsyncClient, admin_token: str,
):
    """비식별화는 리스로 시작을 판정한다. 시작한 시도의 회수를 세다가 상한에서 멈춘다."""
    pid = await new_project(client, admin_token, "cap-deid")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        (await s.get(Project, uuid.UUID(pid))).deident_enabled = True
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": {
            "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"}}
        await s.commit()
    async with SessionLocal() as s:
        await open_next(s, await s.get(Video, vid))
        await s.commit()

    for n in range(1, deident_service._MAX_RETRIES + 1):
        async with SessionLocal() as s:
            job = next(j for j in await deident_service.build_jobs(s)
                       if j["video_id"] == str(vid))
        async with SessionLocal() as s:
            assert await deident_service.extend_lease(s, vid, job["token"])
        await _expire(vid, "deident")
        async with SessionLocal() as s:
            await deident_service.reclaim_expired(s)
        v = await _row(vid)
        if n < deident_service._MAX_RETRIES:
            assert (v.deident_status, v.deident_attempts) == ("pending", n)
    assert v.deident_status == "failed"
    assert v.publish_status != "pending", "비식별화를 못 했으면 다음 단계를 열지 않는다"
