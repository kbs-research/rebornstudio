"""분석 요청 게이트와 보류 뒤처리 테스트.

- 규격 검증이 실패한(`probe_status=error`) 영상은 외부로 보내지 않는다. 읽지 못한 파일은
  변환·재생본 단계가 열리지 않으므로 게이트가 없으면 원본이 그대로 외부로 나간다.
- 일시 오류로 보류된 영상에 보류 표시를 남기고, reconciler의 보류 사이클이 그 영상을 다시 요청한다.
  자동 재요청이 없으면 "요청 대기" 영상이 사람이 다시 누를 때까지 멈춰 있다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Video
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.maiu.naver_client import MaiuTransientError
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, set_project_options, upload_video


class _ThrottledOnce(FakeMaiuClient):
    """첫 `throttle_left`번의 요청을 429로 거절하는 가짜 MI."""

    def __init__(self, throttle_left: int) -> None:
        """거절할 횟수를 받는다."""
        super().__init__()
        self.throttle_left = throttle_left
        self.calls = 0

    async def dispatch(self, *, request: dict) -> str:
        """남은 거절 횟수가 있으면 일시 오류를 낸다."""
        self.calls += 1
        if self.throttle_left > 0:
            self.throttle_left -= 1
            raise MaiuTransientError("MAIU POST assets → 429 Throttle Limited", status=429)
        return await super().dispatch(request=request)


@pytest_asyncio.fixture(loop_scope="session")
async def fake():
    """기본 가짜 MI를 넣고 끝나면 싱글턴을 비운다."""
    c = FakeMaiuClient()
    maiu_client.set_client(c)
    yield c
    maiu_client._client_singleton = None


async def _probe(video_id: str, status: str) -> None:
    """영상의 규격 검증 상태를 바로 바꾼다."""
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(video_id))
        v.probe_status = status
        if status == "passed":
            v.duration_seconds = v.duration_seconds or 60.0
        await s.commit()


async def _extra(video_id: str) -> dict:
    """영상 `metadata`를 읽는다."""
    async with SessionLocal() as s:
        return dict((await s.get(Video, UUID(video_id))).extra or {})


async def _jobs(pid: str) -> set[str]:
    """분석 잡이 생긴 영상 id를 모은다."""
    async with SessionLocal() as s:
        rows = await s.scalars(select(AnalysisJob.video_id).where(AnalysisJob.project_id == UUID(pid)))
        return {str(v) for v in rows}


async def test_probe_error_is_not_dispatched(client: AsyncClient, admin_token: str, fake):
    """규격 검증 실패 영상은 ID로 골라도 요청하지 않는다. 반입 상태는 막힘(`blocked`)이다."""
    pid = await new_project(client, admin_token, "probe-err")
    h = {"Authorization": f"Bearer {admin_token}"}
    bad = await upload_video(client, admin_token, pid, title="broken", policy="pre_cleared")
    good = await upload_video(client, admin_token, pid, title="ok", policy="pre_cleared")
    await _probe(bad, "error")
    await _probe(good, "passed")

    rows = (await client.get(f"/projects/{pid}/videos", headers=h)).json()
    bucket = {v["video_id"]: v["ingest_bucket"] for v in rows}
    assert bucket[bad] == "blocked", "규격 검증 실패 영상이 요청 대기로 분류됐다"
    assert bucket[good] == "ready"
    counts = (await client.get(f"/projects/{pid}/videos/buckets", headers=h)).json()
    assert counts["ready"] == 1 and counts["blocked"] == 1

    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=h, json={"video_ids": [bad, good]})
    assert r.status_code == 200, r.text
    assert await _jobs(pid) == {good}


async def test_publish_in_progress_is_preparing(client: AsyncClient, admin_token: str, fake):
    """재생본 확정 중인 영상을 준비 중으로, 확정이 실패한 영상을 막힘으로 분류한다.

    분석 요청 게이트가 막는 영상을 요청 대기로 세지 않는다.
    """
    pid = await new_project(client, admin_token, "pub-prep")
    h = {"Authorization": f"Bearer {admin_token}"}
    vid = await upload_video(client, admin_token, pid, policy="pre_cleared", probed=True)
    failed = await upload_video(client, admin_token, pid, policy="pre_cleared", probed=True)
    async with SessionLocal() as s:
        (await s.get(Video, UUID(vid))).publish_status = "running"
        (await s.get(Video, UUID(failed))).publish_status = "failed"
        await s.commit()
    rows = (await client.get(f"/projects/{pid}/videos", headers=h)).json()
    bucket = {v["video_id"]: v["ingest_bucket"] for v in rows}
    assert bucket == {vid: "preparing", failed: "blocked"}


async def test_deferred_dispatch_is_marked_and_retried(client: AsyncClient, admin_token: str):
    """보류된 영상에 보류 표시를 남기고 보류 사이클이 프로젝트의 클립 길이로 다시 요청하는지 확인한다.

    요청마다 클립 길이를 받지 않으므로 보류 표시에는 길이를 남기지 않는다.
    다시 요청할 때 프로젝트에 저장된 값을 읽는다. 기본값과 다른 값을 DB에 써서 그 값이 잡에 실리는지 본다.
    """
    from api_gateway.services.maiu import service as maiu_service

    throttled = _ThrottledOnce(throttle_left=1)
    maiu_client.set_client(throttled)
    try:
        pid = await new_project(client, admin_token, "deferred")
        await set_project_options(pid, settings={"scene_options": {
            "scene_range": "SHORT", "min_seconds": 12, "max_seconds": 40}})
        h = {"Authorization": f"Bearer {admin_token}"}
        vid = await upload_video(client, admin_token, pid, policy="pre_cleared", probed=True)
        r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=h,
                              json={"video_ids": [vid]})
        assert r.status_code == 200, r.text
        assert r.json()["deferred"] == 1
        mark = (await _extra(vid)).get("dispatch_deferred")
        assert mark and mark["scene_options"] is None, mark
        assert await _jobs(pid) == set()

        async with SessionLocal() as s:
            n = await maiu_service.redispatch_deferred(s, min_age_seconds=0)
        assert n == 1
        assert await _jobs(pid) == {vid}
        assert "dispatch_deferred" not in await _extra(vid)
        async with SessionLocal() as s:
            job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == UUID(vid)))
        assert job.payload["scene_options"]["min_seconds"] == 12
    finally:
        maiu_client._client_singleton = None


async def test_deferred_mark_clears_when_gate_closes(client: AsyncClient, admin_token: str):
    """보류 뒤 영상이 게이트에 걸리면(규격 거절 등) 다시 요청하지 않고 보류 표시를 지운다."""
    from api_gateway.services.maiu import service as maiu_service

    maiu_client.set_client(_ThrottledOnce(throttle_left=1))
    try:
        pid = await new_project(client, admin_token, "deferred-gate")
        h = {"Authorization": f"Bearer {admin_token}"}
        vid = await upload_video(client, admin_token, pid, policy="pre_cleared", probed=True)
        await client.post(f"/projects/{pid}/maiu/dispatch", headers=h, json={"video_ids": [vid]})
        assert "dispatch_deferred" in await _extra(vid)
        await _probe(vid, "rejected")
        async with SessionLocal() as s:
            assert await maiu_service.redispatch_deferred(s, min_age_seconds=0) == 0
        assert "dispatch_deferred" not in await _extra(vid)
        assert await _jobs(pid) == set()
    finally:
        maiu_client._client_singleton = None
