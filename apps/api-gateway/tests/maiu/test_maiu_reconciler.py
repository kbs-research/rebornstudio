"""MAIU 5단계 reconciler 흐름 테스트. Fake 어댑터로 돈다."""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Clip, MediaStageRun, Video, WorkItem
from api_gateway.services.analysis import job_state
from api_gateway.services.joblock import single_runner
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import reconciler as maiu_reconciler
from api_gateway.services.maiu import slot_counter as maiu_slot_counter
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.maiu.naver_client import MaiuTransientError
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.orm.attributes import flag_modified
from tests import _storage
from tests._helpers import mark_probed, put_video_file
from tests.conftest import uniq


@pytest_asyncio.fixture(loop_scope="session")
async def fresh_client():
    """reconciler 테스트마다 FakeMaiuClient를 새로 주입하고 끝나면 싱글턴을 비운다."""
    fc = FakeMaiuClient()
    maiu_client.set_client(fc)
    yield fc
    # 싱글턴을 비운다.
    maiu_client._client_singleton = None


async def _setup_dispatched_video(
    client: AsyncClient, admin_token: str, *, policy: str = "pre_cleared"
) -> dict:
    """프로젝트를 만들고 영상 1개를 올린 뒤 MAIU dispatch 1건을 만든다."""
    slug = f"rec-{uniq('').split('@')[0]}"
    pid = (
        await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"slug": slug, "name": "rec"},
        )
    ).json()["project_id"]
    await _storage.pin_project(pid)
    vid = (
        await client.post(
            f"/projects/{pid}/videos",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": "rec-video", "copyright_policy": policy},
        )
    ).json()["video_id"]
    await put_video_file(client, admin_token, vid, b"x" * 50)
    await mark_probed(vid)
    # MAIU dispatch
    jobs = (
        await client.post(
            f"/projects/{pid}/maiu/dispatch",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"limit": 1},
        )
    ).json()["created"]
    assert jobs, "dispatch가 비어있음"
    return {"project_id": pid, "video_id": vid, "job": jobs[0]}


async def test_full_pipeline_pending_to_done(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """dispatch한 작업이 `pending`부터 `done`까지 단계별로 진행하고 clips를 만드는지 확인한다."""
    setup = await _setup_dispatched_video(client, admin_token, policy="pre_cleared")
    video_id = setup["video_id"]

    # 단계 1: `pending` → `registering`.
    await _run(maiu_reconciler.bulk_check_registrations)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.stage == "registering"
        assert job.external_ref and job.external_ref.startswith("fake-asset-")

    # 단계 1b: `registering` → `registered`. 폴링하면서 import 메타데이터를 반영한다.
    await _run(maiu_reconciler.bulk_poll_registrations)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.stage == "registered"

    # 단계 2: `registered` → `analyzing`.
    await _run(maiu_reconciler.bulk_check_analyses)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.stage == "analyzing"
        assert (job.payload or {}).get("maiu_analysis_id")

    # 단계 2b: `analyzing` → `analyzed`. 폴링으로 넘어간다.
    await _run(maiu_reconciler.bulk_poll_analyses)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.stage == "analyzed"

    # 단계 3: `analyzed` → `summarizing`. scene 단위 clips를 만들고 요약을 요청한다.
    await _run(maiu_reconciler.bulk_check_summaries)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.stage == "summarizing"
        assert job.payload.get("scene_summary_id")
        clips = (await s.scalars(select(Clip).where(Clip.video_id == video_id))).all()
        assert len(clips) == 2, f"FakeMaiuClient summary가 2 scene, got {len(clips)}"
        # 분석 완료 사실이 상태 변경과 같은 트랜잭션에서 outbox로 남는다.
        # 서비스를 분리할 때 이 이벤트가 work 직접 호출을 대체한다.
        from api_gateway.models import DomainEvent
        ev = await s.scalar(select(DomainEvent).where(
            DomainEvent.event_type == "video.analysis_done",
            DomainEvent.target_id == video_id,
        ))
        assert ev is not None and ev.kind == "integration"
        assert ev.payload["clip_count"] == 2
        # 클립은 정상 상태로 생긴다.
        assert all(c.rights_status == "auto_cleared" for c in clips)
        # speakers와 auto_caption을 확인한다.
        assert clips[0].auto_caption is not None
        assert "brief" in clips[0].auto_caption
        assert len(clips[0].speakers) == 1

    # 단계 3b: `summarizing` → `done`. 장면 요약(VLM)을 붙인다.
    await _run(maiu_reconciler.bulk_poll_scene_summaries)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.status == "done"
        clips = (await s.scalars(
            select(Clip).where(Clip.video_id == video_id).order_by(Clip.start_seconds)
        )).all()
        # 각 scene clip에 description(장면 요약)이 붙는다.
        assert all(c.auto_caption.get("summary") for c in clips)


async def test_clips_create_clip_level_work_items(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """reconciler가 끝나면 clips가 허가 상태로 생기고 클립마다 라벨링 티켓이 바로 생기는지 확인한다."""
    from api_gateway.models import Clip

    setup = await _setup_dispatched_video(client, admin_token, policy="pre_cleared")
    video_id = setup["video_id"]

    await _run(maiu_reconciler.bulk_check_registrations)
    await _run(maiu_reconciler.bulk_poll_registrations)
    await _run(maiu_reconciler.bulk_check_analyses)
    await _run(maiu_reconciler.bulk_poll_analyses)
    await _run(maiu_reconciler.bulk_check_summaries)
    await _run(maiu_reconciler.bulk_poll_scene_summaries)

    async with SessionLocal() as s:
        clips = (await s.scalars(select(Clip).where(Clip.video_id == video_id))).all()
        clip_wis = (await s.scalars(
            select(WorkItem).where(WorkItem.video_id == video_id, WorkItem.clip_id.is_not(None))
        )).all()
    # FakeMaiuClient summary의 2 segments가 2 clips가 된다.
    assert len(clips) == 2
    assert all(c.rights_status == "auto_cleared" for c in clips)
    assert {w.clip_id for w in clip_wis} == {c.clip_id for c in clips}


async def test_run_once_advisory_lock(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """다른 실행이 advisory lock을 점유하면 run_once가 건너뛰고, 락이 풀리면 단계를 진행하는지 확인한다."""
    setup = await _setup_dispatched_video(client, admin_token, policy="pre_cleared")
    video_id = setup["video_id"]

    async def _stage() -> str:
        """영상의 분석 잡 단계를 읽는다."""
        async with SessionLocal() as s:
            job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
            return job.stage

    before = await _stage()
    async with single_runner(maiu_reconciler._RECONCILER_LOCK) as got:
        assert got
        async with SessionLocal() as s:
            assert await maiu_reconciler.run_once(s) == {"skipped": 1}
    assert await _stage() == before

    # 카운터를 0으로 되돌려 다음 슬롯을 정상 사이클로 고정한다. 보류 사이클은 단계를 진행하지 않는다.
    maiu_slot_counter.get_counter().reset()
    async with SessionLocal() as s:
        result = await maiu_reconciler.run_once(s)
    assert result["slot"] == "normal"
    assert result["registered"] >= 1
    assert await _stage() != before


async def test_failure_then_deferred_promotion(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """register_asset이 한 번 실패하면 승격하지 않고, 두 번째 실패(`_MAX_RETRY`)에서 deferred로 승격하는지 확인한다."""
    setup = await _setup_dispatched_video(client, admin_token)
    video_id = setup["video_id"]

    # FakeMaiuClient에 실패를 주입한다. 영상의 source_uri를 실패 목록에 넣는다.
    async with SessionLocal() as s:
        r = await s.execute(
            text("SELECT COALESCE(analysis_uri, source_uri) FROM videos "
                 "WHERE video_id = CAST(:v AS uuid)").bindparams(v=video_id)
        )
        uri = r.scalar()
    fresh_client.force_register_fail.add(uri)

    async def _fail_once() -> AnalysisJob:
        """잡을 pending으로 되돌려 등록을 한 번 실패시키고 승격 단계를 돌린 뒤 잡을 읽는다.

        분석 요청이 이미 에셋을 등록했으므로 에셋 번호를 비워야 리컨실러가 등록을 다시 부른다.
        승격 단계는 다른 테스트가 남긴 실패 잡도 처리하므로 반환 건수 대신 이 잡의 값을 본다.
        """
        async with SessionLocal() as s:
            await s.execute(
                text("UPDATE analysis_job SET stage='pending', status='pending', "
                     "external_ref=NULL WHERE video_id = CAST(:v AS uuid)")
                .bindparams(v=video_id)
            )
            await s.commit()
        await _run(maiu_reconciler.bulk_check_registrations)
        await _run(maiu_reconciler.promote_failures_to_deferred)
        async with SessionLocal() as s:
            return await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))

    first = await _fail_once()
    assert (first.stage, first.attempt, first.deferred) == ("failed", 1, False)
    second = await _fail_once()
    assert (second.stage, second.attempt, second.deferred) == ("failed", 2, True)


async def test_transient_error_does_not_fail_job(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient, monkeypatch
):
    """429(Throttle Limited) 같은 일시 오류는 job을 failed로 만들지 않는다.

    폴링은 반복해서 요청하므로 요청량 제한이 정상 경로에서도 생긴다.
    요청량 제한을 분석 실패로 처리하면 NCP에서는 분석이 정상 진행 중인데
    화면에 분석 실패와 재분석 필요가 표시된다.
    """
    setup = await _setup_dispatched_video(client, admin_token)
    video_id = setup["video_id"]

    # 등록·분석까지 진행시켜 analyzing 상태를 만든다.
    await _run(maiu_reconciler.bulk_check_registrations)
    await _run(maiu_reconciler.bulk_poll_registrations)
    await _run(maiu_reconciler.bulk_check_analyses)

    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.stage == "analyzing"
        before_retry = job.attempt
        asset = job.external_ref

    # 이 잡의 폴링만 요청량 제한으로 실패시킨다. NCP에서는 분석이 계속 진행 중인 상황이다.
    # 같은 DB에 앞선 테스트가 남긴 분석 중 잡이 있으면 그 잡이 배치 앞쪽에 온다. 그 잡까지 429를
    # 받으면 요청량 제한 게이트가 같은 연결의 남은 잡을 건너뛰어 이 잡이 조회되지 않는다.
    original = fresh_client.get_analyze_status

    async def _throttled(asset_id: str):
        """이 잡의 분석 상태 조회가 429를 받는 경우를 흉내 낸다. 다른 잡은 원래 응답을 받는다."""
        if asset_id != asset:
            return await original(asset_id)
        raise MaiuTransientError("MAIU GET analyze-status → 429 Throttle Limited", status=429)

    monkeypatch.setattr(fresh_client, "get_analyze_status", _throttled)
    await _run(maiu_reconciler.bulk_poll_analyses)

    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.stage == "analyzing", "일시 오류인데 상태가 바뀌었다"
        assert job.attempt == before_retry, "일시 오류로 retry_count가 올라 deferred 기준이 오염된다"
        assert job.error_msg and "일시" in job.error_msg

    # 요청량 제한이 풀리면 재요청·재과금 없이 이어서 진행해야 한다.
    # 일시 오류에는 백오프가 걸리므로 그 대기가 지난 상황을 만든다.
    # 백오프 자체는 test_transient_error_backs_off_and_skips_until_retry_after가 검증한다.
    monkeypatch.undo()
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        job.payload = {**(job.payload or {}), "retry_after": "1970-01-01T00:00:00+00:00"}
        flag_modified(job, "payload")
        await s.commit()
    await _run(maiu_reconciler.bulk_poll_analyses)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.stage == "analyzed"
        # 단계가 넘어갔으면 그 사이 호출이 성공했다. 그래서 지난 일시 오류 문구를 지운다.
        assert job.error_msg is None, "다음 단계로 넘어간 잡에 일시 오류 문구가 남았다"


async def test_done_job_shows_no_leftover_transient_error(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """끝난 잡에 이전 일시 오류 문구가 남아 있어도 목록의 `error`에 싣지 않는다."""
    setup = await _setup_dispatched_video(client, admin_token)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == setup["video_id"]))
        job_state.advance(job, "done")
        job.error_msg = "[일시] 분석 서비스 요청량 제한을 초과했습니다"
        await s.commit()
    r = await client.get(f"/projects/{setup['project_id']}/maiu/jobs",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    row = next(j for j in r.json() if j["work_item_id"] == setup["video_id"])
    assert row["status"] == "done" and row["error"] is None


async def test_terminal_error_still_fails_job(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient, monkeypatch
):
    """400처럼 요청 자체가 틀린 오류는 잡을 failed로 만든다. 모든 오류를 일시 오류로 처리하지 않는다."""
    setup = await _setup_dispatched_video(client, admin_token)
    video_id = setup["video_id"]

    await _run(maiu_reconciler.bulk_check_registrations)
    await _run(maiu_reconciler.bulk_poll_registrations)
    await _run(maiu_reconciler.bulk_check_analyses)

    async def _bad_request(_asset_id: str):
        """분석 상태 조회가 400을 받는 경우를 흉내 낸다."""
        raise RuntimeError("MAIU GET analyze-status → 400 유효하지 않은 요청입니다.")

    monkeypatch.setattr(fresh_client, "get_analyze_status", _bad_request)
    await _run(maiu_reconciler.bulk_poll_analyses)

    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.status == "failed"
        assert job.attempt >= 1


async def test_retry_deferred_resumes_without_reanalyze(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """retry_deferred가 deferred 행을 pending으로 리셋하지 않고 재개하는지 확인한다.

    asset_id만 있고 analysis_id는 없는(등록만 된) job은 `registered`로 돌아가야 한다.
    pending으로 되돌리면 `register_asset`이 새 asset을 또 만들고 다시 분석해 과금이 두 배가 된다.
    """
    from api_gateway.services.maiu.reconciler import retry_deferred

    setup = await _setup_dispatched_video(client, admin_token)
    video_id = setup["video_id"]

    # deferred=true와 failed 상태를 직접 만든다.
    # dispatch 직후라 asset_id만 있고 analysis_id는 없다.
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE analysis_job SET deferred=true, stage='failed', status='failed', "
            "attempt=2 WHERE video_id = CAST(:v AS uuid)"
        ).bindparams(v=video_id))
        await s.commit()

    async with SessionLocal() as s:
        n = await retry_deferred(s)
    assert n >= 1

    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.deferred is False
        assert job.stage == "registered"   # registered로 재개해 재등록·재분석을 막는다
        assert job.status != "pending"
        assert job.attempt == 0
        assert job.error_msg is None


async def _run(fn) -> int:
    """새 세션으로 단계 함수 하나를 실행하고 처리 건수를 돌려준다."""
    async with SessionLocal() as s:
        return await fn(s)


async def _drive_to_summarizing(video_id: str) -> None:
    """dispatch된 영상을 summarizing 단계까지 진행시킨다. 요약 폴링은 호출자가 한다."""
    for fn in (
        maiu_reconciler.bulk_check_registrations,
        maiu_reconciler.bulk_poll_registrations,
        maiu_reconciler.bulk_check_analyses,
        maiu_reconciler.bulk_poll_analyses,
        maiu_reconciler.bulk_check_summaries,
    ):
        await _run(fn)


async def test_scenes_past_the_video_end_are_fitted(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """가짜 MI의 고정 장면(0~30초·30~60초)이 40초 영상에서 영상 끝까지로 줄어드는지 확인한다.

    줄이지 않으면 납품 패키지에 원본보다 긴 클립이 실리고 납품 분량(초)이 실제보다 커진다.
    """
    setup = await _setup_dispatched_video(client, admin_token, policy="pre_cleared")
    video_id = setup["video_id"]
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(video_id))
        v.duration_seconds = 40.0
        await s.commit()

    await _drive_to_summarizing(video_id)

    async with SessionLocal() as s:
        clips = (await s.scalars(
            select(Clip).where(Clip.video_id == video_id).order_by(Clip.start_seconds)
        )).all()
        assert [(float(c.start_seconds), float(c.end_seconds)) for c in clips] == [
            (0.0, 30.0), (30.0, 40.0),
        ]


async def test_summary_poll_goes_straight_to_done(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """요약 폴링이 끝나면 잡이 바로 done이 되고 클립마다 요약이 붙는지 확인한다."""
    setup = await _setup_dispatched_video(client, admin_token, policy="pre_cleared")
    video_id = setup["video_id"]

    await _drive_to_summarizing(video_id)
    await _run(maiu_reconciler.bulk_poll_scene_summaries)

    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.status == "done"
        clips = (await s.scalars(select(Clip).where(Clip.video_id == video_id))).all()
        assert all(c.auto_caption.get("summary") for c in clips)


async def test_transient_error_backs_off_and_skips_until_retry_after(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """일시 오류를 받은 job은 `retry_after`까지 배치에서 빠진다.

    이 대기가 없으면 5초 주기로 같은 job을 계속 요청해 요청량 제한이 풀리지 않는다.
    그러면 영상이 한 단계도 넘어가지 못한다.
    """
    from datetime import UTC, datetime, timedelta

    from api_gateway.database import SessionLocal
    from api_gateway.models import AnalysisJob
    from api_gateway.services.maiu import reconciler
    from api_gateway.services.maiu.naver_client import MaiuTransientError
    from sqlalchemy import select

    setup = await _setup_dispatched_video(client, admin_token)
    video_id = setup["video_id"]
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        job_state.advance(job, "registering")
        job.deferred = False
        job.payload = {}
        flag_modified(job, "payload")
        await s.commit()
        job_id = job.id

    # 1회차: 일시 오류면 상태는 그대로 두고 백오프만 건다. 실패로 확정하지 않는다.
    async with SessionLocal() as s:
        job = await s.get(AnalysisJob, job_id)
        await reconciler._on_stage_error(job, MaiuTransientError("429 throttled", status=429), db=s)
        await s.commit()
        assert job.stage == "registering"
        assert job.attempt == 0
        first_delay = job.payload["retry_after"]
        assert job.payload["transient_streak"] == 1

    # 백오프 중에는 배치가 가져가지 않는다.
    async with SessionLocal() as s:
        picked = await reconciler._lock_batch(s, stage="registering")
        assert job_id not in {j.id for j in picked}

    # 같은 단계에서 또 막히면 간격이 늘어난다.
    async with SessionLocal() as s:
        job = await s.get(AnalysisJob, job_id)
        await reconciler._on_stage_error(job, MaiuTransientError("429 again", status=429), db=s)
        await s.commit()
        assert job.payload["transient_streak"] == 2
        assert job.payload["retry_after"] > first_delay

    # retry_after가 지나면 다시 가져간다.
    async with SessionLocal() as s:
        job = await s.get(AnalysisJob, job_id)
        job.payload = {**job.payload,
                       "retry_after": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
        flag_modified(job, "payload")
        await s.commit()
    async with SessionLocal() as s:
        picked = await reconciler._lock_batch(s, stage="registering")
        assert job_id in {j.id for j in picked}


def test_classify_stage_status_catches_failed_variants():
    """MAIU가 돌려주는 실제 값 `FAILED`를 실패로 분류한다.

    `FAIL`만 실패로 보면 `FAILED`가 pending으로 분류된다.
    그러면 MAIU가 실패로 보고한 자산을 계속 진행 중으로 판단하고 폴링한다.
    """
    from api_gateway.services.maiu.naver_client import (
        _UPLOAD_FAIL,
        _UPLOAD_OK,
    )
    from api_gateway.services.maiu.naver_client import (
        classify_stage_status as c,
    )

    for bad in ("FAILED", "FAIL", "UPLOAD_FAILED", "CANCELED", "ANALYZE_FAILED"):
        assert c(bad, ok=_UPLOAD_OK, fail=_UPLOAD_FAIL) == "failed", bad
    for good in ("COMPLETED", "UPLOAD_COMPLETED"):
        assert c(good, ok=_UPLOAD_OK, fail=_UPLOAD_FAIL) == "ok", good
    # 정의되지 않은 값은 pending으로 분류하고 로그를 남긴다. 로그로 MAIU의 상태 값 변경을 알아챈다.
    for unknown in ("PROCESSING", "", None):
        assert c(unknown, ok=_UPLOAD_OK, fail=_UPLOAD_FAIL) == "pending", unknown


def test_documented_in_progress_statuses_do_not_warn(caplog):
    """문서화된 분석 진행 중 값은 경고 없이 `pending`으로 판정한다.

    분석 요청 직후 상태는 `BEFORE_ANALYZE`·`WAIT_ANALYZE`를 거쳐 `ANALYZING`이 된다.
    앞의 두 값을 모르는 값으로 보면 폴링 주기마다 경고가 쌓여 실제로 모르는 값을 찾기 어려워진다.
    진행 중 값은 단계별이라 분석 값은 등록 상태 판정에서 여전히 모르는 값이다.
    """
    import logging

    from api_gateway.services.maiu.naver_client import (
        _ANALYZE_FAIL,
        _ANALYZE_OK,
        _ANALYZE_PENDING,
        _UPLOAD_FAIL,
        _UPLOAD_OK,
        _UPLOAD_PENDING,
    )
    from api_gateway.services.maiu.naver_client import (
        classify_stage_status as c,
    )

    caplog.set_level(logging.WARNING, logger="api_gateway.services.maiu.naver_client")
    for status in ("BEFORE_ANALYZE", "WAIT_ANALYZE", "ANALYZING"):
        assert c(status, ok=_ANALYZE_OK, fail=_ANALYZE_FAIL, pending=_ANALYZE_PENDING) == "pending"
    assert c("UPLOADING", ok=_UPLOAD_OK, fail=_UPLOAD_FAIL, pending=_UPLOAD_PENDING) == "pending"
    assert not caplog.records, [r.getMessage() for r in caplog.records]

    assert c("WAIT_ANALYZE", ok=_UPLOAD_OK, fail=_UPLOAD_FAIL, pending=_UPLOAD_PENDING) == "pending"
    assert any("WAIT_ANALYZE" in r.getMessage() for r in caplog.records), \
        "등록 단계에 분석 값이 오면 경고로 드러나야 한다"


async def test_retry_failed_reuses_asset_when_registered(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """분석 실패를 재시도할 때 기존 자산을 재사용한다.

    자산이 이미 MI에 있는데 등록부터 다시 하면 같은 영상이 두 번 올라간다.
    그러면 나중에 어느 자산이 맞는지 구분할 수 없고 업로드·전송도 다시 일어난다.
    그래서 잡을 `registered`로만 되돌린다. reconciler가 같은 asset_id로 분석을 다시 요청한다.
    """
    from api_gateway.services.maiu.service import retry_failed

    setup = await _setup_dispatched_video(client, admin_token, policy="pre_cleared")
    pid, vid = setup["project_id"], setup["video_id"]

    await _run(maiu_reconciler.bulk_check_registrations)
    await _run(maiu_reconciler.bulk_poll_registrations)

    async with SessionLocal() as s:
        job = (await s.scalars(select(AnalysisJob).where(AnalysisJob.video_id == vid))).one()
        asset = job.external_ref
        assert asset, "등록이 안 끝났다 — 전제가 성립하지 않는다"
        job_state.advance(job, "failed")           # 분석 실패 상황
        await s.commit()

    async with SessionLocal() as s:
        r = await retry_failed(s, UUID(pid), [vid], None)
    assert r == {"requeued": 1, "restarted": 0, "resummarized": 0,
                 "resummarize_failed": 0, "skipped": 0}

    async with SessionLocal() as s:
        job = (await s.scalars(select(AnalysisJob).where(AnalysisJob.video_id == vid))).one()
    assert job.stage == "registered", "분석부터 다시 걸리지 않았다"
    assert job.external_ref == asset, "자산이 바뀌었다 — 재등록이 일어났다"


async def test_retry_failed_restarts_when_no_asset(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """자산이 없으면(등록 자체가 실패) 잡을 지우고 디스패치 티켓을 다시 `available`로 만든다.

    그러면 처음부터 다시 진행한다.
    """
    from api_gateway.services.maiu.service import retry_failed

    setup = await _setup_dispatched_video(client, admin_token, policy="pre_cleared")
    pid, vid = setup["project_id"], setup["video_id"]

    async with SessionLocal() as s:
        job = (await s.scalars(select(AnalysisJob).where(AnalysisJob.video_id == vid))).one()
        job_state.advance(job, "failed")
        job.external_ref = None
        await s.commit()

    async with SessionLocal() as s:
        r = await retry_failed(s, UUID(pid), [vid], None)
    assert r == {"requeued": 0, "restarted": 1, "resummarized": 0,
                 "resummarize_failed": 0, "skipped": 0}

    async with SessionLocal() as s:
        assert (await s.scalars(
            select(AnalysisJob).where(AnalysisJob.video_id == vid)
        )).first() is None, "잡이 남아 있다"
        item = (await s.scalars(
            select(WorkItem).where(WorkItem.video_id == vid, WorkItem.clip_id.is_(None))
        )).one()
    assert item.status == "available", "디스패치 티켓이 안 살아났다"


async def test_register_run_records_request_gates(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """MI 등록 런에 요청 게이트 결과를 세부 항목으로 남기는지 확인한다.

    `_dispatch_core`는 요청 전에 권리·규격·변환·원본을 확인한다.
    이 확인을 런에 남기지 않으면 통과한 영상에는 기록이 없고 막힌 영상은 `dispatch_error` 문자열에만 남는다.
    게이트는 등록 직전에 하는 일이라 새 단계를 더하지 않고 등록 단계의 세부 항목으로 둔다.
    런은 자산 등록 요청이 성공한 뒤에 열린다. MI가 거절하면 등록이 시작되지 않아 런도 없다.
    이때 거절 사유는 `dispatch_error`에 남는다.
    """
    setup = await _setup_dispatched_video(client, admin_token, policy="pre_cleared")
    vid = setup["video_id"]

    async with SessionLocal() as s:
        run = (await s.scalars(
            select(MediaStageRun).where(
                MediaStageRun.video_id == vid, MediaStageRun.stage == "register",
            )
        )).first()
        video = await s.get(Video, UUID(vid))

    assert run is not None, "등록 런이 안 열렸다"
    d = run.detail or {}
    # 요청 시점의 영상 상태를 기록한다. 이 테스트는 요청 뒤에 영상 상태를 바꾸지 않으므로 지금 값과 같다.
    assert "gate_rights" not in d, "영상 권리 게이트는 기록하지 않는다"
    assert d.get("gate_probe") == video.probe_status == "passed"
    assert d.get("gate_transcode") == video.transcode_status
    assert d.get("gate_source") == "확인됨"



async def _drive_to_analyzed(video_id: str) -> None:
    """dispatch된 영상을 analyzed 단계까지 진행시킨다."""
    for fn in (
        maiu_reconciler.bulk_check_registrations,
        maiu_reconciler.bulk_poll_registrations,
        maiu_reconciler.bulk_check_analyses,
        maiu_reconciler.bulk_poll_analyses,
    ):
        await _run(fn)


async def _clear_backoff(video_id: str) -> None:
    """일시 오류로 걸린 대기 시각을 지워 다음 배치가 잡을 바로 가져가게 한다."""
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        job.payload = {k: v for k, v in (job.payload or {}).items() if k != "retry_after"}
        flag_modified(job, "payload")
        await s.commit()


async def test_transient_summary_fetch_keeps_job_analyzed(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """결과 조회가 일시 오류로 실패하면 잡이 analyzed에 머물고 다음 사이클에 클립을 만든다.

    단계를 먼저 summarizing으로 옮기면 요약 폴링이 요약 요청 번호가 없는 잡으로 보고
    클립 없이 done으로 닫는다. 그러면 영상 티켓은 잡힌 채 남고 보존 정책이 원본을 폐기한다.
    """
    setup = await _setup_dispatched_video(client, admin_token, policy="pre_cleared")
    video_id = setup["video_id"]
    await _drive_to_analyzed(video_id)

    original = fresh_client.get_summary

    async def flaky(analysis_id: str) -> dict:
        """첫 호출에만 일시 오류를 낸다."""
        raise MaiuTransientError("503 unavailable", status=503)

    fresh_client.get_summary = flaky
    try:
        await _run(maiu_reconciler.bulk_check_summaries)
    finally:
        fresh_client.get_summary = original
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.stage == "analyzed", job.stage
        assert job.in_flight_count == 0

    # 요약 폴링이 돌아도 analyzed 잡은 건드리지 않는다.
    await _run(maiu_reconciler.bulk_poll_scene_summaries)
    await _clear_backoff(video_id)
    await _run(maiu_reconciler.bulk_check_summaries)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.stage == "summarizing"
        clips = (await s.scalars(select(Clip).where(Clip.video_id == video_id))).all()
        assert len(clips) == 2


async def test_summary_poll_reopens_job_without_clips(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """요약 요청 번호도 클립도 없는 summarizing 잡은 done으로 닫지 않고 analyzed로 되돌린다.

    클립 없이 done으로 닫으면 분석이 끝난 것처럼 보이지만 라벨링할 클립이 하나도 없다.
    """
    setup = await _setup_dispatched_video(client, admin_token, policy="pre_cleared")
    video_id = setup["video_id"]
    await _drive_to_analyzed(video_id)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        job_state.advance(job, "summarizing")
        job.in_flight_count = 1
        await s.commit()

    await _run(maiu_reconciler.bulk_poll_scene_summaries)
    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
        assert job.stage == "analyzed", job.stage
        assert job.in_flight_count == 0

    await _run(maiu_reconciler.bulk_check_summaries)
    async with SessionLocal() as s:
        clips = (await s.scalars(select(Clip).where(Clip.video_id == video_id))).all()
        assert len(clips) == 2


async def test_unknown_summary_failure_status_closes_job(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """요약 상태가 FAILED·CANCELED처럼 정확한 집합 밖의 실패 값이면 잡을 done으로 닫는다.

    정확한 집합만 비교하면 나머지 값이 모두 진행 중으로 남아 잡이 경고 없이 계속 폴링된다.
    """
    for bad in ("FAILED", "SUMMARIZE_CANCELED"):
        setup = await _setup_dispatched_video(client, admin_token, policy="pre_cleared")
        video_id = setup["video_id"]
        await _drive_to_summarizing(video_id)

        async def failed(analysis_id: str, summary_id: str, _bad: str = bad) -> str:
            """정해진 실패 상태를 돌려준다."""
            return _bad

        fresh_client.check_scene_summary_status = failed
        try:
            await _run(maiu_reconciler.bulk_poll_scene_summaries)
        finally:
            del fresh_client.check_scene_summary_status
        async with SessionLocal() as s:
            job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == video_id))
            assert job.stage == "done", (bad, job.stage)
            clips = (await s.scalars(select(Clip).where(Clip.video_id == video_id))).all()
            assert len(clips) == 2


def test_documented_summary_progress_statuses_stay_pending(caplog):
    """문서화된 장면 요약 진행 중 값은 경고 없이 `pending`으로 판정한다."""
    import logging

    from api_gateway.services.maiu.naver_client import (
        _SUMMARY_FAIL,
        _SUMMARY_OK,
        _SUMMARY_PENDING,
    )
    from api_gateway.services.maiu.naver_client import (
        classify_stage_status as c,
    )

    caplog.set_level(logging.WARNING, logger="api_gateway.services.maiu.naver_client")
    for status in ("BEFORE", "WAIT_SUMMARIZE", "SUMMARIZING"):
        assert c(status, ok=_SUMMARY_OK, fail=_SUMMARY_FAIL, pending=_SUMMARY_PENDING) == "pending"
    assert not caplog.records, [r.getMessage() for r in caplog.records]
    assert c("SUMMARIZE_COMPLETED", ok=_SUMMARY_OK, fail=_SUMMARY_FAIL) == "ok"
