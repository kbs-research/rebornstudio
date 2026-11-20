"""분석 요청 한 번에 MI 에셋이 한 번만 등록되는지 확인한다.

분석 요청(`dispatch`)은 요청 시점에 에셋을 등록하고 그 번호를 잡에 기록한다.
리컨실러가 같은 잡을 다시 등록하면 에셋이 둘 생긴다. 첫 에셋은 쓰이지 않은 채 남는다.
MI는 에셋마다 과금하므로 중복 등록은 정상 경로에서 비용을 두 번 낸다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, DomainEvent
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import reconciler as maiu_reconciler
from api_gateway.services.maiu.client import FakeMaiuClient
from httpx import AsyncClient
from sqlalchemy import select, text
from tests._helpers import new_project, upload_video


class _CountingClient(FakeMaiuClient):
    """등록 요청을 세는 가짜 MI."""

    def __init__(self) -> None:
        """등록한 URI 목록을 비운 채 시작한다."""
        super().__init__()
        self.registered: list[str] = []

    async def register_asset(self, *, video_uri: str) -> str:
        """등록 URI를 적고 가짜 MI의 등록을 그대로 부른다."""
        self.registered.append(video_uri)
        return await super().register_asset(video_uri=video_uri)

    def times(self, video_id: str) -> int:
        """이 영상의 등록 요청 수를 센다. 리컨실러가 같은 DB의 다른 잡도 처리하므로 영상 id로 거른다."""
        return sum(video_id in uri for uri in self.registered)


@pytest_asyncio.fixture(loop_scope="session")
async def counting():
    """등록을 세는 가짜 MI를 넣고 끝나면 싱글턴을 비운다."""
    c = _CountingClient()
    maiu_client.set_client(c)
    yield c
    maiu_client._client_singleton = None


async def _run(fn) -> int:
    """새 세션으로 단계 함수 하나를 실행하고 처리 건수를 돌려준다."""
    async with SessionLocal() as s:
        return await fn(s)


async def _dispatch(client: AsyncClient, token: str) -> tuple[str, dict]:
    """영상 하나를 올려 분석 요청하고 (video_id, 만든 잡)을 돌려준다."""
    pid = await new_project(client, token, "reg-once")
    vid = await upload_video(client, token, pid, probed=True)
    r = await client.post(f"/projects/{pid}/maiu/dispatch",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"video_ids": [vid]})
    assert r.status_code == 200, r.text
    created = r.json()["created"]
    assert len(created) == 1, r.text
    return vid, created[0]


async def test_dispatch_registers_the_asset_once(
    client: AsyncClient, admin_token: str, counting: _CountingClient
):
    """리컨실러가 요청이 만든 에셋을 이어 쓰는지 확인한다. 등록은 한 번이고 잡의 에셋 번호는 바뀌지 않는다."""
    vid, job = await _dispatch(client, admin_token)
    asset = job["maiu_asset_id"]
    assert asset and counting.times(vid) == 1

    for fn in (maiu_reconciler.bulk_check_registrations,
               maiu_reconciler.bulk_poll_registrations,
               maiu_reconciler.bulk_check_analyses):
        await _run(fn)

    assert counting.times(vid) == 1, counting.registered
    async with SessionLocal() as s:
        row = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == UUID(vid)))
        assert row.external_ref == asset
        assert row.stage == "analyzing"
        requested = await s.scalar(select(DomainEvent).where(
            DomainEvent.event_type == "maiu.requested", DomainEvent.target_id == UUID(vid)))
        assert requested.payload["asset_id"] == asset


async def test_dispatched_job_starts_waiting_for_import(
    client: AsyncClient, admin_token: str, counting: _CountingClient
):
    """요청 직후 잡이 등록 완료 대기(`registering`) 단계에 있는지 확인한다. 다음 폴링은 import 상태만 확인한다."""
    vid, _ = await _dispatch(client, admin_token)
    async with SessionLocal() as s:
        row = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == UUID(vid)))
        assert row.stage == "registering"
        assert row.status == "running"
        assert row.in_flight_count == 1


async def test_pending_job_with_asset_is_not_registered_again(
    client: AsyncClient, admin_token: str, counting: _CountingClient
):
    """에셋 번호가 이미 있는 대기 잡을 다시 등록하지 않고 등록 완료 대기로 옮긴다.

    대기 단계에 남은 잡이 에셋 번호를 가지고 있으면 다음 사이클이 같은 영상을 한 번 더 등록하지 않게 한다.
    """
    vid, job = await _dispatch(client, admin_token)
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE analysis_job SET stage='pending', status='pending', in_flight_count=0 "
            "WHERE video_id = CAST(:v AS uuid)").bindparams(v=vid))
        await s.commit()

    await _run(maiu_reconciler.bulk_check_registrations)

    assert counting.times(vid) == 1, counting.registered
    async with SessionLocal() as s:
        row = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == UUID(vid)))
        assert row.external_ref == job["maiu_asset_id"]
        assert row.stage == "registering"
        assert row.in_flight_count == 1
