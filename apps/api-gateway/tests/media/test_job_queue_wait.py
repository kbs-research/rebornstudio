"""큐에서 기다리는 잡이 리스 만료로 회수되지 않는지, 회수한 시도의 메시지를 취소하는지 확인하는 테스트.

투입한 잡은 워커가 가져가기 전까지 큐에서 기다린다. 앞선 긴 변환이 워커를 점유하면 그 대기가
리스(5분)보다 길어진다. 투입할 때부터 5분 리스를 걸면 대기 중에 회수되고 새 토큰으로 다시 투입된다.
그러면 큐에 남은 이전 메시지도 결국 실행되어 같은 영상을 두 워커가 끝까지 변환한다.

그래서 리스는 두 단계로 건다.

  1. 투입 때는 잡 URL의 유효 기간만큼 넉넉히 건다. 큐에서 기다리는 동안 회수되지 않는다.
  2. 워커가 시작하자마자 보내는 하트비트가 평소 리스(5분)로 줄인다. 그 뒤로 워커가 비정상
     종료되면 5분 안에 회수된다.

회수할 때는 이전 토큰을 취소 목록으로 넘긴다. 큐 메시지의 id가 토큰이므로 늦게 가져간
이전 메시지는 워커가 시작 전에 버린다.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import MediaStageRun, Project, Video
from api_gateway.services.media import deident as deident_service
from api_gateway.services.media import publish as publish_service
from api_gateway.services.media import transcode as transcode_service
from api_gateway.services.media.pipeline import open_next
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, reset_transcode_active, upload_video

_SRC = {"video_codec": "av1", "width": 1920, "height": 1080, "duration_seconds": 600.0,
        "start_time_seconds": 0.0, "format_name": "mov,mp4,m4a,3gp,3g2,mj2"}


@pytest.fixture(autouse=True)
async def _isolate():
    """앞 테스트가 남긴 변환·재생본·비식별화 활성 행을 비운다."""
    await reset_transcode_active()
    yield


async def _video(client: AsyncClient, token: str, name: str, **values) -> uuid.UUID:
    """영상 하나를 올리고 주어진 열 값을 넣는다."""
    pid = await new_project(client, token, name)
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": _SRC}
        for k, val in values.items():
            setattr(v, k, val)
        await s.commit()
    return vid


async def _deident_video(client: AsyncClient, token: str, name: str) -> uuid.UUID:
    """비식별화를 켠 프로젝트에 영상을 올리고 비식별화 대기까지 연다."""
    pid = await new_project(client, token, name)
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        (await s.get(Project, uuid.UUID(pid))).deident_enabled = True
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": {
            "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"}}
        await s.commit()
    async with SessionLocal() as s:
        await open_next(s, await s.get(Video, vid))
        await s.commit()
    return vid


#: 단계 이름별 (서비스, 영상 준비 함수). 세 단계가 같은 규칙을 지키는지 함께 확인한다.
_STAGES = {
    "transcode": (transcode_service,
                  lambda c, t, n: _video(c, t, n, transcode_status="pending")),
    "publish": (publish_service,
                lambda c, t, n: _video(c, t, n, transcode_status="done",
                                       publish_status="pending")),
    "deident": (deident_service, _deident_video),
}


async def _dispatch(service, vid: uuid.UUID) -> dict:
    """그 단계의 잡을 만들어 이 영상의 잡을 돌려준다."""
    async with SessionLocal() as s:
        jobs = await service.build_jobs(s)
    return next(j for j in jobs if j["video_id"] == str(vid))


async def _lease_left(vid: uuid.UUID, stage: str) -> float:
    """그 단계 리스가 지금부터 몇 초 남았는지 돌려준다."""
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        return (getattr(v, f"{stage}_lease_until") - datetime.now(UTC)).total_seconds()


@pytest.mark.parametrize("stage", list(_STAGES))
async def test_a_queued_job_is_not_reclaimed_while_it_waits(
    client: AsyncClient, admin_token: str, stage: str,
):
    """투입 때 건 리스는 큐 대기를 덮는다. 시작 하트비트가 평소 리스로 줄인다."""
    service, seed = _STAGES[stage]
    vid = await seed(client, admin_token, f"qwait-{stage}")
    job = await _dispatch(service, vid)

    assert await _lease_left(vid, stage) > transcode_service._LEASE_S * 10, \
        "큐에서 기다리는 동안 5분 리스가 끝나 회수된다"

    async with SessionLocal() as s:
        assert await service.extend_lease(s, vid, job["token"], done_seconds=0.0) is True
    assert await _lease_left(vid, stage) <= transcode_service._LEASE_S + 5


@pytest.mark.parametrize("stage", list(_STAGES))
async def test_reclaim_hands_the_old_token_to_revoke(
    client: AsyncClient, admin_token: str, stage: str,
):
    """회수는 이전 토큰을 취소 목록으로 넘긴다. 큐에 남은 이전 메시지를 워커가 시작 전에 버린다."""
    service, seed = _STAGES[stage]
    vid = await seed(client, admin_token, f"qrevoke-{stage}")
    job = await _dispatch(service, vid)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        setattr(v, f"{stage}_lease_until", datetime.now(UTC) - timedelta(seconds=1))
        await s.commit()

    revoked: list[str] = []
    async with SessionLocal() as s:
        assert await service.reclaim_expired(s, revoke=revoked.extend) >= 1
    assert job["token"] in revoked
    async with SessionLocal() as s:
        assert getattr(await s.get(Video, vid), f"{stage}_status") == "pending"


@pytest.mark.parametrize("stage", ["transcode", "publish"])
async def test_a_job_that_never_started_is_not_called_a_lost_server(
    client: AsyncClient, admin_token: str, stage: str,
):
    """시작 하트비트가 한 번도 오지 않은 시도를 "처리 서버가 작업을 시작하지 못했습니다"로 기록한다.

    이 경우 처리 서버는 연결돼 있고 그 잡을 가져가지 못한 상태다.
    "응답이 끊겼습니다"로 적으면 원인을 잘못 표시한다.
    """
    service, seed = _STAGES[stage]
    vid = await seed(client, admin_token, f"qnever-{stage}")
    await _dispatch(service, vid)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        setattr(v, f"{stage}_lease_until", datetime.now(UTC) - timedelta(seconds=1))
        await s.commit()
    async with SessionLocal() as s:
        await service.reclaim_expired(s)
    async with SessionLocal() as s:
        run = (await s.scalars(
            select(MediaStageRun)
            .where(MediaStageRun.video_id == vid, MediaStageRun.stage == stage)
            .order_by(MediaStageRun.id.desc()).limit(1))).one()
    assert run.status == "failed" and run.detail.get("transient") is True
    assert "끊겼습니다" not in (run.error or "")
    assert run.error == "처리 서버가 작업을 시작하지 못했습니다."
