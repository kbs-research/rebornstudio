"""가짜 MI 클라이언트가 프로세스를 나눠도 같은 답을 주는지 보는 테스트.

로컬 개발과 compose 개발 스택에서는 api가 에셋을 등록하고 워커가 상태를 조회한다. 두 프로세스는
각자 `FakeMaiuClient` 인스턴스를 쓰므로, 가짜가 상태를 인스턴스 메모리에 두면 워커가 에셋을 찾지 못해
잡이 `registering`에 머문다. 여기서는 단계마다 새 인스턴스를 넣어 두 프로세스를 흉내 낸다.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Clip
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import reconciler as maiu_reconciler
from api_gateway.services.maiu.client import FakeMaiuClient
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, upload_video


@pytest_asyncio.fixture(loop_scope="session")
async def reset_singleton():
    """테스트가 바꾼 클라이언트 싱글턴을 끝나고 비운다."""
    yield
    maiu_client.set_client(None)


async def test_fresh_instance_sees_assets_and_analyses_of_another():
    """한 인스턴스가 발급한 에셋·분석 id를 새 인스턴스가 등록 완료·분석 완료로 읽는다."""
    asset = await FakeMaiuClient().register_asset(video_uri="s3://bucket/intake/v/source.mp4")

    worker = FakeMaiuClient()
    assert await worker.check_registration(asset) == "registered"
    imported = await worker.get_import_status(asset)
    assert imported["uploadStatus"] == "COMPLETED" and imported["videoId"] == asset

    analysis = await worker.submit_analysis(asset)
    later = FakeMaiuClient()
    assert await later.check_analysis(analysis) == "analyzed"
    assert len((await later.get_summary(analysis))["segments"]) == 2
    assert len((await later.get_summary(f"{analysis}:1001"))["segments"]) == 2
    detail = await FakeMaiuClient().get_index_detail(asset, "1001")
    assert detail["indexId"] == 1001 and len(detail["indexDetails"]["segments"]) == 2


async def test_ids_not_issued_by_the_fake_stay_unknown():
    """가짜가 발급하지 않은 id에는 실제 MI가 없는 에셋에 주는 응답을 돌려준다."""
    fake = FakeMaiuClient()
    assert await fake.check_registration("asset-test") == "unknown"
    assert await fake.get_import_status("asset-test") == {}
    assert await fake.check_analysis("9999:1") == "unknown"
    with pytest.raises(RuntimeError):
        await fake.get_summary("9999:1")


async def test_pipeline_completes_when_each_stage_runs_in_a_new_process(
    client: AsyncClient, admin_token: str, reset_singleton,
):
    """api 인스턴스가 등록한 잡을 단계마다 새 인스턴스로 돌려도 `done`까지 가고 클립 두 개를 만든다."""
    pid = await new_project(client, admin_token, "fake-xproc")
    vid = await upload_video(client, admin_token, pid, probed=True)
    maiu_client.set_client(FakeMaiuClient())
    r = await client.post(f"/projects/{pid}/maiu/dispatch",
                          headers={"Authorization": f"Bearer {admin_token}"}, json={"limit": 1})
    assert r.status_code == 200 and r.json()["created"], r.text

    for stage in (
        maiu_reconciler.bulk_poll_registrations,
        maiu_reconciler.bulk_check_analyses,
        maiu_reconciler.bulk_poll_analyses,
        maiu_reconciler.bulk_check_summaries,
        maiu_reconciler.bulk_poll_scene_summaries,
    ):
        maiu_client.set_client(FakeMaiuClient())
        async with SessionLocal() as s:
            await stage(s)

    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == vid))
        assert job.status == "done", (job.stage, job.status, job.error_msg)
        clips = (await s.scalars(select(Clip).where(Clip.video_id == vid))).all()
    assert len(clips) == 2
