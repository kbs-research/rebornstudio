"""폴링 배치의 요청량 제한 차단(`ThrottleGate`) 테스트.

분석이 여럿 진행 중이면 한 사이클에서 같은 연결로 잡마다 상태를 조회한다. 잡별 백오프는 429를 받은
그 잡만 미룬다. 그래서 차단이 없으면 같은 사이클의 나머지 잡이 계속 요청량 제한에 걸리고
일시 오류 기록이 잡 수만큼 쌓인다. 두 가지를 확인한다.

  1. 한 배치에서 429를 받으면 같은 연결의 남은 잡은 조회하지 않고 아무것도 기록하지 않는다.
  2. 다른 연결의 잡은 막지 않는다. 429가 아닌 일시 오류(5xx)는 막지 않는다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, DomainEvent
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import reconciler as maiu_reconciler
from api_gateway.services.maiu.client import JOB_TARGET_KEY, FakeMaiuClient
from api_gateway.services.maiu.naver_client import MaiuTransientError
from api_gateway.services.maiu.reconciler import errors as reconciler_errors
from api_gateway.services.maiu.reconciler import stages as reconciler_stages
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, upload_video


@pytest_asyncio.fixture(loop_scope="session")
async def fake():
    """가짜 분석 서비스를 넣고 끝나면 싱글턴을 비운다."""
    c = FakeMaiuClient()
    maiu_client.set_client(c)
    yield c
    maiu_client._client_singleton = None


async def _run(fn) -> int:
    """새 세션으로 단계 함수 하나를 실행한다."""
    async with SessionLocal() as s:
        return await fn(s)


async def _analyzing_jobs(client: AsyncClient, token: str, n: int) -> list[int]:
    """한 프로젝트에 영상 n개를 요청하고 분석 중까지 진행시킨 잡 id를 돌려준다."""
    pid = await new_project(client, token, "poll-gate")
    vids = [await upload_video(client, token, pid, policy="pre_cleared", probed=True)
            for _ in range(n)]
    r = await client.post(f"/projects/{pid}/maiu/dispatch",
                          headers={"Authorization": f"Bearer {token}"}, json={"video_ids": vids})
    assert r.status_code == 200 and len(r.json()["created"]) == n, r.text
    for fn in (maiu_reconciler.bulk_check_registrations, maiu_reconciler.bulk_poll_registrations,
               maiu_reconciler.bulk_check_analyses):
        await _run(fn)
    async with SessionLocal() as s:
        jobs = (await s.scalars(select(AnalysisJob).where(
            AnalysisJob.video_id.in_([UUID(v) for v in vids])).order_by(AnalysisJob.id))).all()
    assert [j.stage for j in jobs] == ["analyzing"] * n
    return [j.id for j in jobs]


async def test_one_throttle_stops_the_rest_of_the_batch(
    client: AsyncClient, admin_token: str, fake: FakeMaiuClient, monkeypatch,
):
    """429를 받은 뒤 같은 연결의 남은 잡은 조회하지 않고 일시 오류도 남기지 않는다."""
    ids = await _analyzing_jobs(client, admin_token, 3)

    async def _only_mine(db, *, stage):
        """이 테스트의 잡만 배치로 준다. 같은 DB에 있는 다른 테스트의 잡을 건드리지 않는다."""
        return (await db.scalars(select(AnalysisJob).where(
            AnalysisJob.id.in_(ids), AnalysisJob.stage == stage).order_by(AnalysisJob.id))).all()

    calls: list[str] = []

    async def _throttled(asset_id: str):
        """상태 조회를 항상 요청량 제한으로 실패시킨다."""
        calls.append(asset_id)
        raise MaiuTransientError("MAIU GET analyze-status → 429 Throttle Limited", status=429)

    monkeypatch.setattr(reconciler_stages, "_lock_batch", _only_mine)
    monkeypatch.setattr(fake, "get_analyze_status", _throttled)
    await _run(maiu_reconciler.bulk_poll_analyses)

    assert len(calls) == 1, f"제한을 받은 뒤에도 같은 연결로 {len(calls)}번 조회했다"
    async with SessionLocal() as s:
        jobs = (await s.scalars(select(AnalysisJob).where(AnalysisJob.id.in_(ids))
                                .order_by(AnalysisJob.id))).all()
        events = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "maiu.stage.transient_error",
            DomainEvent.target_id.in_([j.video_id for j in jobs])))).all()
    assert jobs[0].payload.get("transient_streak") == 1
    assert all("retry_after" not in (j.payload or {}) for j in jobs[1:]), "건너뛴 잡에 백오프가 걸렸다"
    assert len(events) == 1, "건너뛴 잡에도 일시 오류가 기록됐다"


def _job(connection: str | None) -> AnalysisJob:
    """분석 선택만 기록한 저장하지 않는 잡을 만든다."""
    return AnalysisJob(payload={JOB_TARGET_KEY: {"connection_id": connection, "workspace": "ws"}})


def test_gate_blocks_only_the_throttled_connection():
    """다른 연결의 잡은 막지 않고, 429가 아닌 일시 오류는 막지 않는다."""
    gate = reconciler_errors.ThrottleGate()
    a, a2, b = _job("11111111-1111-1111-1111-111111111111"), \
        _job("11111111-1111-1111-1111-111111111111"), _job("22222222-2222-2222-2222-222222222222")
    gate.note(a, MaiuTransientError("503", status=503))
    assert not gate.blocked(a2), "429가 아닌 일시 오류로 막았다"
    gate.note(a, MaiuTransientError("429", status=429))
    assert gate.blocked(a2) and not gate.blocked(b)
