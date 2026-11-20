"""MAIU reconciler의 sweep_stale_in_flight 테스트."""
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.maiu.reconciler import sweep_stale_in_flight
from httpx import AsyncClient
from tests._helpers import new_project, upload_video


@pytest_asyncio.fixture(loop_scope="session")
async def fresh_client():
    """테스트마다 새 FakeMaiuClient를 주입하고 끝나면 싱글턴을 비운다."""
    fc = FakeMaiuClient()
    maiu_client.set_client(fc)
    yield fc
    maiu_client._client_singleton = None


async def test_sweep_stale_analyzing_resumes_polling_not_pending(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """오래 갱신되지 않은 analyzing 작업을 pending으로 리셋하지 않고 analyzing으로 다시 폴링한다.

    analysis_id가 있으면 analyze가 이미 트리거된 작업이므로 재폴링만 한다.
    48분 길이의 영상 분석은 정상이어도 stale 임계값(10분)을 넘는다.
    이 작업을 멈춘 것으로 보고 pending으로 리셋하면 `register_asset`이 새 asset을 또 만들고
    다시 분석해 과금이 두 배가 된다.
    """
    pid = await new_project(client, admin_token, "sweep")
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        job = AnalysisJob(
            adapter_key="maiu",
            video_id=UUID(vid), project_id=UUID(pid),
            stage="analyzing", status="running",
            external_ref="9999",
            payload={"maiu_analysis_id": "9999:1"},   # analyze가 이미 트리거된 상태
            in_flight_count=3,
            deferred=False,
        )
        s.add(job)
        await s.flush()
        # updated_at을 15분 전으로 되돌린다. server_default가 now()라서 raw UPDATE로 바꾼다.
        await s.execute(AnalysisJob.__table__.update().where(
            AnalysisJob.id == job.id
        ).values(updated_at=datetime.now(UTC) - timedelta(minutes=15)))
        await s.commit()
        job_id = job.id

    async with SessionLocal() as s:
        swept = await sweep_stale_in_flight(s)
        assert swept >= 1
        refreshed = await s.get(AnalysisJob, job_id)
        assert refreshed is not None
        assert refreshed.stage == "analyzing"   # 재폴링한다. pending으로 가면 재분석된다.
        assert refreshed.status not in ("pending", "registered")
        assert refreshed.deferred is False        # deferred도 걸지 않는다
        assert refreshed.in_flight_count == 0


async def test_sweep_stale_registered_resumes_registered_not_pending(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """analyze 전의 stale 작업을 registered로 되돌린다. pending으로 되돌리면 다시 등록한다."""
    pid = await new_project(client, admin_token, "sweep-reg")
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        job = AnalysisJob(
            adapter_key="maiu",
            video_id=UUID(vid), project_id=UUID(pid),
            stage="registering", status="running",
            external_ref="8888",     # 등록은 끝났고 analyze 전이다(analysis_id 없음)
            in_flight_count=1,
            deferred=False,
        )
        s.add(job)
        await s.flush()
        await s.execute(AnalysisJob.__table__.update().where(
            AnalysisJob.id == job.id
        ).values(updated_at=datetime.now(UTC) - timedelta(minutes=15)))
        await s.commit()
        job_id = job.id

    async with SessionLocal() as s:
        assert await sweep_stale_in_flight(s) >= 1
        refreshed = await s.get(AnalysisJob, job_id)
        assert refreshed.stage == "registered"   # analyze 트리거를 기다리는 단계다.
        assert refreshed.status != "pending"


def test_resume_status_never_reregisters_when_asset_exists():
    """_resume_status는 asset이나 analysis 마커가 있으면 pending을 돌려주지 않는다."""
    from api_gateway.services.maiu.reconciler import _resume_status

    class _J:
        """`_resume_status`가 읽는 속성만 흉내 낸다. 분석 handle은 `payload`에 있다."""

        def __init__(self, asset, analysis):
            """asset은 external_ref에, analysis는 payload에 둔다."""
            self.external_ref = asset
            self.payload = {"maiu_analysis_id": analysis}

    assert _resume_status(_J("a", "a:1")) == "analyzing"   # analyze가 트리거됐으면 다시 폴링한다
    assert _resume_status(_J("a", None)) == "registered"   # 등록만 했으면 analyze를 처음 요청한다
    assert _resume_status(_J(None, None)) == "pending"     # 둘 다 없으면 처음부터 등록한다


async def test_sweep_skips_recent_in_flight(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """방금 갱신된 in_flight 작업을 sweep 대상에서 뺀다."""
    pid = await new_project(client, admin_token, "sweep-recent")
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        job = AnalysisJob(
            adapter_key="maiu",
            video_id=UUID(vid), project_id=UUID(pid),
            stage="analyzing", status="running",
            external_ref="1234",
            payload={"maiu_analysis_id": "1234:1"},
            in_flight_count=2,
            deferred=False,
        )
        s.add(job)
        await s.commit()
        job_id = job.id

    async with SessionLocal() as s:
        await sweep_stale_in_flight(s)
        refreshed = await s.get(AnalysisJob, job_id)
        assert refreshed is not None
        # 스윕하면 in_flight_count가 0이 된다. 최근 행은 건너뛰므로 값이 그대로다.
        assert refreshed.in_flight_count == 2
        assert refreshed.deferred is False
