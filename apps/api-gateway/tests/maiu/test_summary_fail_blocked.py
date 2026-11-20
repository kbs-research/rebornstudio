"""장면 요약을 받지 못한 영상의 막힘 분류와 요약 재요청 테스트. 가짜 MI 클라이언트로 돈다.

요약 실패 경로 셋(요약 요청 실패, MI의 요약 실패 응답, 요약 요청 번호 없이 클립만 있는 잡)은
모두 잡을 `done`으로 닫는다. 영상은 단계 이력의 요약 실패로 막힘이 되어야 하고,
요약 재요청은 분석을 다시 하지 않고 요약만 다시 요청해야 한다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Clip, DomainEvent, MediaStageRun, Video, WorkItem
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import reconciler as maiu_reconciler
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.maiu.service import retry_failed
from api_gateway.services.media.video import (
    _ingest_bucket_case,
    _prep_blocked_stage_case,
    _prep_stage_case,
)
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from tests import _storage
from tests._helpers import mark_probed, put_video_file
from tests.conftest import uniq


@pytest_asyncio.fixture(loop_scope="session")
async def fake_mi():
    """테스트마다 가짜 MI 클라이언트를 새로 주입하고 끝나면 싱글턴을 비운다."""
    fc = FakeMaiuClient()
    maiu_client.set_client(fc)
    yield fc
    maiu_client._client_singleton = None


async def _dispatched_video(client: AsyncClient, admin_token: str) -> tuple[str, str]:
    """프로젝트와 영상 한 개를 만들고 MI 분석을 요청한다. (프로젝트 id, 영상 id)를 돌려준다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    slug = f"sumfail-{uniq('').split('@')[0]}"
    pid = (await client.post("/projects", headers=h, json={"slug": slug, "name": "sumfail"})
           ).json()["project_id"]
    await _storage.pin_project(pid)
    vid = (await client.post(f"/projects/{pid}/videos", headers=h,
                             json={"title": "sumfail-video", "copyright_policy": "pre_cleared"})
           ).json()["video_id"]
    await put_video_file(client, admin_token, vid, b"x" * 50)
    await mark_probed(vid)
    created = (await client.post(f"/projects/{pid}/maiu/dispatch", headers=h, json={"limit": 1})
               ).json()["created"]
    assert created, "분석 요청이 만들어지지 않았다"
    return pid, vid


async def _run(fn) -> int:
    """새 세션으로 단계 함수 하나를 실행한다."""
    async with SessionLocal() as s:
        return await fn(s)


async def _drive(vid: str, *, through_summary_request: bool) -> None:
    """요청한 영상을 analyzed까지, 또는 요약 요청(summarizing)까지 진행한다."""
    fns = [
        maiu_reconciler.bulk_check_registrations,
        maiu_reconciler.bulk_poll_registrations,
        maiu_reconciler.bulk_check_analyses,
        maiu_reconciler.bulk_poll_analyses,
    ]
    if through_summary_request:
        fns.append(maiu_reconciler.bulk_check_summaries)
    for fn in fns:
        await _run(fn)


async def _classes(vid: str) -> tuple[str, str, str | None]:
    """영상의 (준비 여정 축, 반입 상태 축, 막힌 위치)를 서버 판정 그대로 읽는다."""
    async with SessionLocal() as s:
        row = (await s.execute(
            select(_prep_stage_case(), _ingest_bucket_case(), _prep_blocked_stage_case())
            .select_from(Video).where(Video.video_id == UUID(vid))
        )).one()
    prep, bucket, at = row
    return prep, bucket, at if prep == "blocked" else None


async def _job(vid: str) -> AnalysisJob:
    """영상의 MI 잡을 읽는다."""
    async with SessionLocal() as s:
        return await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == UUID(vid)))


async def _count(model, vid: str) -> int:
    """영상에 딸린 행 수를 센다."""
    async with SessionLocal() as s:
        return int(await s.scalar(
            select(func.count()).select_from(model).where(model.video_id == UUID(vid))))


async def _fail_by_verdict(fake: FakeMaiuClient, vid: str) -> None:
    """요약 요청까지 진행한 뒤 MI가 요약 실패를 알리게 한다."""
    await _drive(vid, through_summary_request=True)

    async def failed(analysis_id: str, summary_id: str) -> str:
        """요약 실패 상태를 돌려준다."""
        return "SUMMARIZE_FAIL"

    fake.check_scene_summary_status = failed
    try:
        await _run(maiu_reconciler.bulk_poll_scene_summaries)
    finally:
        del fake.check_scene_summary_status


async def test_summary_request_failure_blocks_video(
    client: AsyncClient, admin_token: str, fake_mi: FakeMaiuClient
):
    """요약 요청이 실패해 잡이 done으로 닫힌 영상이 두 축 모두 막힘(summary)인지 확인한다."""
    _, vid = await _dispatched_video(client, admin_token)
    await _drive(vid, through_summary_request=False)

    async def refuse(analysis_id: str, scene_ids=None) -> str:
        """요약 요청을 거절한다."""
        raise RuntimeError("MAIU POST scene-summary → 500 {}")

    fake_mi.request_scene_summary = refuse
    try:
        await _run(maiu_reconciler.bulk_check_summaries)
    finally:
        del fake_mi.request_scene_summary

    assert (await _job(vid)).stage == "done"
    assert await _count(Clip, vid) == 2
    assert await _classes(vid) == ("blocked", "blocked", "summary")


async def test_summary_fail_verdict_blocks_video(
    client: AsyncClient, admin_token: str, fake_mi: FakeMaiuClient
):
    """MI가 요약 실패를 알려 잡이 done으로 닫힌 영상이 막힘(summary)인지 확인한다.

    화면 사유에는 MI의 상태 값과 분석 실패 머리말을 싣지 않는다.
    """
    _, vid = await _dispatched_video(client, admin_token)
    await _fail_by_verdict(fake_mi, vid)

    assert (await _job(vid)).stage == "done"
    assert await _classes(vid) == ("blocked", "blocked", "summary")
    async with SessionLocal() as s:
        run = await s.scalar(
            select(MediaStageRun).where(MediaStageRun.video_id == UUID(vid),
                                        MediaStageRun.stage == "summarize")
            .order_by(MediaStageRun.id.desc()).limit(1))
    assert run.status == "failed"
    assert run.error == "장면 요약을 만들지 못했습니다"


async def test_summary_skipped_without_request_blocks_video(
    client: AsyncClient, admin_token: str, fake_mi: FakeMaiuClient
):
    """요약 요청 번호 없이 클립만 있는 summarizing 잡이 done으로 닫힌 뒤 막힘(summary)인지 확인한다."""
    _, vid = await _dispatched_video(client, admin_token)
    await _drive(vid, through_summary_request=True)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == UUID(vid)))
        job.payload = {k: v for k, v in job.payload.items() if k != "scene_summary_id"}
        await s.commit()

    await _run(maiu_reconciler.bulk_poll_scene_summaries)

    assert (await _job(vid)).stage == "done"
    assert await _classes(vid) == ("blocked", "blocked", "summary")


async def test_summary_received_video_is_ready(
    client: AsyncClient, admin_token: str, fake_mi: FakeMaiuClient
):
    """요약을 받은 영상이 준비 완료(ready_for_work)·완료(done)인지 확인한다."""
    _, vid = await _dispatched_video(client, admin_token)
    await _drive(vid, through_summary_request=True)
    await _run(maiu_reconciler.bulk_poll_scene_summaries)
    assert await _classes(vid) == ("ready_for_work", "done", None)


async def test_retry_resummarizes_only_and_returns_to_ready(
    client: AsyncClient, admin_token: str, fake_mi: FakeMaiuClient
):
    """요약 재요청이 요약만 한 번 다시 요청하고, 요약이 붙으면 영상이 준비 완료로 돌아오는지 확인한다.

    분석 재요청(registered로 되돌리기)과 등록·분석 호출이 일어나지 않고, 클립과 work_item 수가 그대로다.
    """
    pid, vid = await _dispatched_video(client, admin_token)
    await _fail_by_verdict(fake_mi, vid)
    clips_before = await _count(Clip, vid)
    items_before = await _count(WorkItem, vid)
    aid_before = (await _job(vid)).payload["maiu_analysis_id"]

    calls: dict[str, list] = {"summary": [], "register": [], "analyze": []}

    async def request_summary(analysis_id: str, scene_ids=None) -> str:
        """요약 요청을 세고 새 요청 번호를 돌려준다."""
        calls["summary"].append(analysis_id)
        return "summary-2"

    async def register(**kw) -> str:
        """등록 호출을 센다."""
        calls["register"].append(kw)
        return "asset-x"

    async def analyze(*a, **kw) -> str:
        """분석 호출을 센다."""
        calls["analyze"].append(a)
        return "analysis-x"

    fake_mi.request_scene_summary = request_summary
    fake_mi.register_asset = register
    fake_mi.submit_analysis = analyze
    try:
        async with SessionLocal() as s:
            r = await retry_failed(s, UUID(pid), [UUID(vid)], None)
        assert r == {"requeued": 0, "restarted": 0, "resummarized": 1,
                     "resummarize_failed": 0, "skipped": 0}
        assert calls["summary"] == [aid_before], "같은 분석 번호로 요약을 한 번만 요청해야 한다"
        # 활동 이력에서 분석 재시도로 읽히지 않게 요약 재요청은 종류를 나눠 남긴다.
        async with SessionLocal() as s2:
            kinds = set((await s2.scalars(select(DomainEvent.event_type).where(
                DomainEvent.target_id == UUID(vid)))).all())
        assert "maiu.summary_retried" in kinds
        assert "maiu.retried" not in kinds

        job = await _job(vid)
        assert job.stage == "summarizing"
        assert job.payload["scene_summary_id"] == "summary-2"
        assert await _classes(vid) == ("summarizing", "analyzing", None)

        # 같은 영상을 다시 눌러도 요약 중인 잡은 고르지 않는다.
        async with SessionLocal() as s:
            again = await retry_failed(s, UUID(pid), [UUID(vid)], None)
        assert again["skipped"] == 1
        assert len(calls["summary"]) == 1

        # reconciler가 요약을 붙이고 done으로 닫는다.
        for fn in (
            maiu_reconciler.bulk_check_registrations,
            maiu_reconciler.bulk_check_analyses,
            maiu_reconciler.bulk_check_summaries,
            maiu_reconciler.bulk_poll_scene_summaries,
        ):
            await _run(fn)
    finally:
        del fake_mi.request_scene_summary
        del fake_mi.register_asset
        del fake_mi.submit_analysis

    assert calls["register"] == [] and calls["analyze"] == [], "분석을 다시 요청했다"
    assert (await _job(vid)).stage == "done"
    assert await _classes(vid) == ("ready_for_work", "done", None)
    assert await _count(Clip, vid) == clips_before
    assert await _count(WorkItem, vid) == items_before
    async with SessionLocal() as s:
        clips = (await s.scalars(select(Clip).where(Clip.video_id == UUID(vid)))).all()
    assert all(c.auto_caption.get("summary") for c in clips)


async def test_retry_resummarize_request_failure_keeps_blocked(
    client: AsyncClient, admin_token: str, fake_mi: FakeMaiuClient
):
    """요약 재요청을 MI가 받지 않으면 잡은 done으로 남고 영상은 막힘으로 남는지 확인한다."""
    pid, vid = await _dispatched_video(client, admin_token)
    await _fail_by_verdict(fake_mi, vid)

    async def refuse(analysis_id: str, scene_ids=None) -> str:
        """요약 요청을 거절한다."""
        raise RuntimeError("MAIU POST scene-summary → 500 {}")

    fake_mi.request_scene_summary = refuse
    try:
        async with SessionLocal() as s:
            r = await retry_failed(s, UUID(pid), [UUID(vid)], None)
    finally:
        del fake_mi.request_scene_summary
    assert r["resummarize_failed"] == 1 and r["resummarized"] == 0
    job = await _job(vid)
    assert job.stage == "done"
    assert job.in_flight_count == 0
    assert await _classes(vid) == ("blocked", "blocked", "summary")


async def test_timeline_summary_row_retryable_follows_server(
    client: AsyncClient, admin_token: str, fake_mi: FakeMaiuClient
):
    """준비 타임라인의 요약 실패 줄은 요약 재요청을 받을 수 있을 때만 `retryable`인지 확인한다."""
    pid, vid = await _dispatched_video(client, admin_token)
    await _fail_by_verdict(fake_mi, vid)
    h = {"Authorization": f"Bearer {admin_token}"}

    async def summarize_row() -> dict:
        """타임라인의 요약 줄을 읽는다."""
        res = await client.get(f"/videos/{vid}/prep-timeline", headers=h)
        assert res.status_code == 200, res.text
        return next(e for e in res.json()["entries"] if e["stage"] == "summarize")

    row = await summarize_row()
    assert row["status"] == "failed" and row["retryable"] is True
    assert row["error"] == "장면 요약을 만들지 못했습니다"

    # 클립이 사라지면 요약 재요청이 받지 않으므로 버튼을 열지 않는다(`no_clips` 막힘).
    async with SessionLocal() as s:
        await s.execute(delete(WorkItem).where(
            WorkItem.video_id == UUID(vid), WorkItem.clip_id.is_not(None)))
        await s.execute(delete(Clip).where(Clip.video_id == UUID(vid)))
        await s.commit()
    assert await _classes(vid) == ("blocked", "blocked", "no_clips")
    row = await summarize_row()
    assert row["status"] == "failed" and row["retryable"] is False
