"""MI 폴링 강도 테스트. 요청량 제한(429)을 부르는 반복 호출을 줄이는 다섯 규칙을 확인한다.

  1. 진행 중인 잡은 잡마다 다음 조회 시각을 두고, 그 시각이 지난 잡만 배치에 넣는다.
  2. 한 사이클은 httpx 클라이언트 하나를 같이 쓰고 끝나면 닫는다.
  3. 429를 받은 연결은 다음 사이클에도 Retry-After(없으면 30초) 동안 조회하지 않는다.
  4. 일시 오류 감사 이벤트는 연속이 시작될 때 한 번만 남긴다.
  5. 외부 id를 받은 잡은 그 잡을 끝낼 때 바로 커밋한다. 뒤의 잡이 실패해도 id가 남는다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from api_gateway import redis_infra
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, DomainEvent
from api_gateway.services.analysis import job_state
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import naver_client
from api_gateway.services.maiu import reconciler as maiu_reconciler
from api_gateway.services.maiu.client import JOB_TARGET_KEY, FakeMaiuClient
from api_gateway.services.maiu.naver_client import MaiuTransientError, NaverMaiuClient
from api_gateway.services.maiu.reconciler import errors as reconciler_errors
from api_gateway.services.maiu.reconciler import stages as reconciler_stages
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
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


async def _dispatched(client: AsyncClient, token: str, n: int) -> list[int]:
    """한 프로젝트에 영상 n개의 분석을 요청하고 잡 id를 돌려준다."""
    pid = await new_project(client, token, "poll-intensity")
    vids = [await upload_video(client, token, pid, policy="pre_cleared", probed=True)
            for _ in range(n)]
    r = await client.post(f"/projects/{pid}/maiu/dispatch",
                          headers={"Authorization": f"Bearer {token}"}, json={"video_ids": vids})
    assert r.status_code == 200 and len(r.json()["created"]) == n, r.text
    async with SessionLocal() as s:
        jobs = (await s.scalars(select(AnalysisJob).where(
            AnalysisJob.video_id.in_([UUID(v) for v in vids])).order_by(AnalysisJob.id))).all()
    return [j.id for j in jobs]


def _only(ids: list[int]):
    """이 테스트의 잡만 배치로 주는 `_lock_batch` 대역을 만든다. 같은 DB에 있는 다른 테스트의 잡을 건드리지 않는다."""
    async def _mine(db, *, stage):
        """`stage` 단계의 이 테스트 잡을 id 순서로 돌려준다."""
        return (await db.scalars(select(AnalysisJob).where(
            AnalysisJob.id.in_(ids), AnalysisJob.stage == stage).order_by(AnalysisJob.id))).all()
    return _mine


async def _to_analyzing(ids: list[int], monkeypatch) -> None:
    """요청한 잡을 분석 중 단계까지 진행시킨다."""
    monkeypatch.setattr(reconciler_stages, "_lock_batch", _only(ids))
    for fn in (maiu_reconciler.bulk_check_registrations, maiu_reconciler.bulk_poll_registrations,
               maiu_reconciler.bulk_check_analyses):
        await _run(fn)


async def test_in_progress_jobs_wait_for_their_next_poll(
    client: AsyncClient, admin_token: str, fake: FakeMaiuClient, monkeypatch,
):
    """진행 중이라는 응답을 받은 잡은 다음 조회 시각(30~60초 뒤)까지 배치에서 빠진다."""
    ids = await _dispatched(client, admin_token, 1)
    await _to_analyzing(ids, monkeypatch)

    async def _busy(asset_id: str) -> dict:
        """분석이 아직 진행 중이라는 응답을 흉내 낸다."""
        return {"status": "ANALYZING"}

    monkeypatch.setattr(fake, "get_analyze_status", _busy)
    await _run(maiu_reconciler.bulk_poll_analyses)
    async with SessionLocal() as s:
        job = await s.get(AnalysisJob, ids[0])
    due = datetime.fromisoformat(job.payload["next_poll_at"])
    wait = (due - datetime.now(UTC)).total_seconds()
    assert 20 <= wait <= 61, wait
    assert wait < settings.maiu_sweep_stale_minutes * 60, "멈춘 잡 판정 기준보다 길다"

    monkeypatch.undo()
    async with SessionLocal() as s:
        picked = await reconciler_stages._lock_batch(s, stage="analyzing")
        assert ids[0] not in {j.id for j in picked}, "다음 조회 시각 전에 배치에 넣었다"
        job = await s.get(AnalysisJob, ids[0])
        job.payload = {**job.payload,
                       "next_poll_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
        flag_modified(job, "payload")
        await s.commit()
    async with SessionLocal() as s:
        picked = await reconciler_stages._lock_batch(s, stage="analyzing")
        assert ids[0] in {j.id for j in picked}


async def test_a_cycle_shares_one_http_client(monkeypatch):
    """한 사이클 안의 요청은 httpx 클라이언트 하나를 같이 쓰고, 사이클이 끝나면 그 클라이언트를 닫는다."""
    created: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    def _counting(*args, **kwargs):
        """만든 클라이언트를 기록하고 네트워크 대신 고정 응답을 준다."""
        kwargs["transport"] = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"result": {"uploadStatus": "UPLOADING"}}))
        cli = real(*args, **kwargs)
        created.append(cli)
        return cli

    monkeypatch.setattr(naver_client.httpx, "AsyncClient", _counting)
    mi = NaverMaiuClient(access_key="a", secret_key="s", workspace="w", project_id="p")
    async with naver_client.shared_session():
        await mi.get_import_status("1")
        await mi.get_import_status("2")
    assert len(created) == 1, f"한 사이클에 클라이언트를 {len(created)}개 만들었다"
    assert created[0].is_closed, "사이클이 끝났는데 클라이언트가 열려 있다"


def test_throttle_is_remembered_across_cycles(monkeypatch):
    """429를 받은 연결은 다음 사이클의 게이트에서도 Retry-After 동안 막힌다. 다른 연결은 막지 않는다."""
    monkeypatch.setattr(settings, "maiu_enabled", True)
    conn = "33333333-3333-3333-3333-333333333333"
    job = AnalysisJob(payload={JOB_TARGET_KEY: {"connection_id": conn, "workspace": "ws"}})
    other = AnalysisJob(payload={JOB_TARGET_KEY: {
        "connection_id": "44444444-4444-4444-4444-444444444444", "workspace": "ws"}})
    r = redis_infra.get_redis()
    key = reconciler_errors.throttle_key((conn, "ws"))
    try:
        reconciler_errors.ThrottleGate().note(
            job, MaiuTransientError("429", status=429, retry_after=7))
        nxt = reconciler_errors.ThrottleGate()
        assert nxt.blocked(job), "다음 사이클이 같은 연결을 바로 다시 조회한다"
        assert not nxt.blocked(other)
        assert 0 < r.ttl(key) <= 7
    finally:
        r.delete(key)


def test_retry_after_header_is_read():
    """Retry-After 헤더의 초 값을 읽고, 읽을 수 없는 값은 버린다."""
    assert naver_client._retry_after_s("12") == 12
    assert naver_client._retry_after_s("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert naver_client._retry_after_s(None) is None


async def test_transient_event_is_written_when_a_streak_starts(
    client: AsyncClient, admin_token: str, fake: FakeMaiuClient, monkeypatch,
):
    """같은 단계의 일시 오류가 이어져도 감사 이벤트는 연속이 시작될 때 한 번만 남긴다."""
    ids = await _dispatched(client, admin_token, 1)
    await _to_analyzing(ids, monkeypatch)

    async def _down(asset_id: str):
        """분석 서비스가 일시적으로 응답하지 못하는 경우를 흉내 낸다."""
        raise MaiuTransientError("MAIU GET analyze-status → 503", status=503)

    monkeypatch.setattr(fake, "get_analyze_status", _down)
    for _ in range(2):
        await _run(maiu_reconciler.bulk_poll_analyses)
        async with SessionLocal() as s:
            job = await s.get(AnalysisJob, ids[0])
            job.payload = {k: v for k, v in job.payload.items() if k != "retry_after"}
            flag_modified(job, "payload")
            await s.commit()
    async with SessionLocal() as s:
        job = await s.get(AnalysisJob, ids[0])
        events = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "maiu.stage.transient_error",
            DomainEvent.target_id == job.video_id))).all()
    assert job.payload["transient_streak"] == 2
    assert len(events) == 1, f"연속 일시 오류마다 이벤트를 {len(events)}개 남겼다"


class _Crash(BaseException):
    """처리 도중 워커가 비정상 종료되는 경우를 흉내 내는 예외. `except Exception`이 잡지 않는다."""


async def test_registered_asset_id_survives_a_crash_later_in_the_batch(
    client: AsyncClient, admin_token: str, fake: FakeMaiuClient, monkeypatch,
):
    """앞 잡이 받은 에셋 id는 뒤 잡에서 워커가 비정상 종료되어도 남는다. 남지 않으면 다시 등록해 고아 에셋이 생긴다."""
    ids = await _dispatched(client, admin_token, 2)
    async with SessionLocal() as s:
        for jid in ids:
            job = await s.get(AnalysisJob, jid)
            job_state.advance(job, "pending")
            job.external_ref = None
        await s.commit()
    monkeypatch.setattr(reconciler_stages, "_lock_batch", _only(ids))
    original = fake.register_asset
    calls: list[str] = []

    async def _second_crashes(*, video_uri: str) -> str:
        """첫 등록은 성공시키고 두 번째 등록에서 워커가 비정상 종료된다."""
        calls.append(video_uri)
        if len(calls) == 2:
            raise _Crash()
        return await original(video_uri=video_uri)

    monkeypatch.setattr(fake, "register_asset", _second_crashes)
    with pytest.raises(_Crash):
        await _run(maiu_reconciler.bulk_check_registrations)
    async with SessionLocal() as s:
        first = await s.get(AnalysisJob, ids[0])
    assert first.external_ref, "앞 잡의 에셋 id가 커밋되지 않았다"
    assert first.stage == "registering"
