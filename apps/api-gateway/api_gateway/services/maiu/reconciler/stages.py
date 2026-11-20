"""MAIU 5단계 reconciler의 단계 함수.

상태 전이(`stage`)와 각 전이에서 부르는 외부 호출:

- `pending` → `registering`: `register_asset()`.
  분석 요청이 이미 에셋을 등록했으면 호출 없이 옮긴다.
- `registering` → `registered`: `get_import_status()`. video.extra와 duration을 갱신한다.
- `registered` → `analyzing`: `submit_analysis()`.
- `analyzing` → `analyzed`: `get_analyze_status()`.
- `analyzed` → `summarizing`: `get_summary()`.
  결과를 받은 뒤에 단계를 옮기고, clips를 INSERT하고 video 메타를 갱신한 뒤 장면 요약을 요청한다.
- `summarizing` → `done`: 장면 요약을 폴링한다.
  요약 요청 번호도 클립도 없는 잡은 `analyzed`로 되돌린다.
  요약을 받지 못한 잡도 `done`으로 닫고 단계 이력에 `summarize` 실패를 남긴다.
  그 영상은 요약을 다시 받을 때까지 막힘으로 분류된다(`media.video._summary_blocked_expr`).
  요약 재요청(`maiu.service.retry_failed`)이 같은 분석 번호로 요약만 다시 요청하고
  잡을 `summarizing`으로 되돌린다.

실패가 `_MAX_RETRY`회 쌓이면 `deferred`가 참이 된다.
보류된 잡은 `retry_deferred`가 안전 상태로 되돌린다.
재시도와 스윕은 `_resume_status`로 되돌릴 단계를 정한다. 이미 끝낸 등록·분석을 반복하지 않으므로
재과금이 생기지 않는다.

폴링 결과가 아직 진행 중이면 `updated_at`을 갱신하고 잡의 다음 조회 시각(`payload.next_poll_at`)을
정한다. 배치는 그 시각이 지난 잡만 고른다(`_lock_batch`). 사이클(5초)마다 모든 진행 중 잡을
조회하면 분석 서비스의 요청량 제한(429)에 걸린다.
배치를 고른 뒤 잡마다 행을 다시 잠그고, 그 잡을 끝내면 바로 커밋한다(`_job_tx`).
그래서 외부 호출 여러 번에 걸쳐 행 잠금을 유지하지 않는다. 받은 외부 id(에셋·분석·요약 요청 번호)도
뒤 잡의 실패나 워커 종료로 사라지지 않는다.
외부 id가 사라지면 다시 등록·요청해 고아 에셋과 중복 과금이 생긴다.
`in_flight_count`는 외부 요청을 시작할 때 올리고 그 단계가 끝날 때 내린다.
`attempt`(DTO의 retry_count)는 확정 실패일 때만 오른다.
FakeMaiuClient는 모든 폴링에 바로 성공을 돌려주므로 통합 테스트는 한 사이클에 단계를 넘긴다.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import AnalysisJob, Clip, Project, Video
from api_gateway.services import audit, error_text, platform_credentials
from api_gateway.services.analysis import job_state, scene_options
from api_gateway.services.analysis.registry import MAIU_KEY
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.naver_client import (
    _ANALYZE_FAIL as _ANALYZE_FAIL_SET,
)
from api_gateway.services.maiu.naver_client import (
    _ANALYZE_OK as _ANALYZE_OK_SET,
)
from api_gateway.services.maiu.naver_client import (
    _ANALYZE_PENDING,
    _SUMMARY_FAIL,
    _SUMMARY_OK,
    _SUMMARY_PENDING,
    _UPLOAD_PENDING,
    classify_stage_status,
)
from api_gateway.services.maiu.naver_client import (
    _UPLOAD_FAIL as _UPLOAD_FAIL_SET,
)
from api_gateway.services.maiu.naver_client import (
    _UPLOAD_OK as _UPLOAD_OK_SET,
)
from api_gateway.services.maiu.reconciler.common import _BATCH, TRANSIENT_MARK, _job_target
from api_gateway.services.maiu.reconciler.errors import ThrottleGate, _on_stage_error
from api_gateway.services.maiu.reconciler.ingest import (
    _apply_import_metadata,
    _attach_scene_summaries,
    _create_clips_from_summary,
)
from api_gateway.services.media import resolver
from api_gateway.services.media import stage_runs as media_stage_runs

_MAX_RETRY = 2  # deferred로 승격하는 실패 횟수
#: 진행 중인 잡의 다음 조회 시각을 담는 `payload` 키(ISO). 단계가 넘어가면 지운다.
_NEXT_POLL_KEY = "next_poll_at"
#: 진행 중인 잡의 조회 간격(초)의 하한과 상한. 잡이 오래될수록 하한에서 상한으로 늘린다.
#: 상한은 멈춘 잡 판정 기준(`maiu_sweep_stale_minutes`, 기본 10분)보다 충분히 짧아야 한다.
#: 조회할 때마다 `updated_at`이 갱신되므로 정상 폴링이 멈춘 잡으로 판정되지 않는다.
_POLL_MIN_S = 30
_POLL_MAX_S = 60
#: 잡의 경과 시간이 이 초만큼 늘 때마다 조회 간격을 1초 늘린다. 10분이면 상한에 닿는다.
_POLL_GROW_EVERY_S = 20
#: 클립 없이 요약 단계에 들어간 잡을 analyzed로 되돌릴 때 준비 타임라인에 남기는 사유.
#: `분석`으로 시작해야 화면 문구 변환이 앞 문구를 붙이지 않는다.
_SUMMARY_REOPENED_TEXT = "분석 결과를 반영하지 못해 결과 조회를 다시 시도합니다"


def _advance_ok(job: AnalysisJob, stage: str) -> None:
    """외부 호출이 성공한 잡을 다음 단계로 옮기고 지난 일시 오류 문구를 지운다.

    일시 오류(`_on_stage_error`)는 상태를 그대로 두고 `error_msg`에 `[일시]` 문구를 남긴다.
    단계가 넘어갔으면 그 뒤 호출이 성공했으므로 일시 오류 문구는 더 이상 맞지 않는다.
    문구를 남겨 두면 끝난 잡이 "요청량 제한을 초과했습니다"를 표시한 채 목록에 남는다.
    확정 실패 문구는 실패 상태에서만 생기고 재시도(`retry_deferred`)가 지운다.
    그래서 여기서는 확정 실패 문구를 지우지 않는다.
    """
    job_state.advance(job, stage)
    if (job.error_msg or "").startswith(TRANSIENT_MARK):
        job.error_msg = None
    if _NEXT_POLL_KEY in (job.payload or {}):
        # 다음 조회 시각은 지난 단계의 폴링 간격이다. 새 단계의 첫 호출을 미루지 않게 지운다.
        job.payload = {k: v for k, v in job.payload.items() if k != _NEXT_POLL_KEY}


def _schedule_next_poll(job: AnalysisJob) -> None:
    """진행 중이라는 응답을 받은 잡의 폴링 시각을 갱신하고 다음 조회 시각을 정한다.

    간격은 `_POLL_MIN_S`에서 시작해 잡이 오래될수록 `_POLL_MAX_S`까지 는다.
    긴 영상일수록 분석이 오래 걸리므로 오래된 잡을 자주 조회해도 얻는 것이 적다.
    """
    now = datetime.now(UTC)
    job.updated_at = now
    age = (now - job.created_at).total_seconds() if job.created_at else 0.0
    interval = min(_POLL_MAX_S, _POLL_MIN_S + int(max(age, 0.0) // _POLL_GROW_EVERY_S))
    job.payload = {**(job.payload or {}),
                   _NEXT_POLL_KEY: (now + timedelta(seconds=interval)).isoformat()}


async def _has_clips(db: AsyncSession, job: AnalysisJob) -> bool:
    """잡의 영상에 클립이 하나라도 있으면 참이다."""
    return await db.scalar(
        select(Clip.clip_id).where(Clip.video_id == job.video_id).limit(1)
    ) is not None


# --- `pending` → `registering`: 단계 1, import 요청 ---------------------

async def bulk_check_registrations(db: AsyncSession) -> int:
    """pending 행의 에셋 등록을 요청한다. 성공하면 stage가 'registering'이 된다.

    에셋 번호가 이미 있는 행은 등록하지 않고 'registering'으로만 옮긴다.
    완료 폴링은 `bulk_poll_registrations`가 한다. 처리 건수를 돌려준다.
    """
    jobs = await _lock_batch(db, stage="pending")
    if not jobs:
        return 0
    # 배치의 video를 한 번에 미리 읽어 N+1 조회를 피한다.
    video_ids = [j.video_id for j in jobs]
    videos = {
        v.video_id: v for v in (
            await db.scalars(select(Video).where(Video.video_id.in_(video_ids)))
        )
    }
    # 요청 시점의 MI 선택이 기록되지 않은 잡에는 등록 전에 프로젝트의 현재 선택을 기록한다.
    # 그래야 등록한 MI 프로젝트와 이후 폴링이 조회하는 MI 프로젝트가 같다.
    unpinned = {j.project_id for j in jobs if maiu_client.target_of_job(j) is None}
    project_settings = {
        p.project_id: p.settings for p in (
            await db.scalars(select(Project).where(Project.project_id.in_(unpinned)))
        )
    } if unpinned else {}
    count = 0
    gate = ThrottleGate()
    for job in jobs:
        if gate.blocked(job):
            continue
        # 잡마다 행을 다시 잠그고 그 잡을 끝내면 바로 커밋한다(`_job_tx`).
        async with _job_tx(db):
            if await _relock(db, job, stage="pending") is None:
                continue
            if job.project_id in project_settings and maiu_client.target_of_job(job) is None:
                target = platform_credentials.analysis_target_of(project_settings[job.project_id])
                await platform_credentials.ensure_loaded(db, target)
                pinned = maiu_client.pinned_target(target).as_dict()
                job.payload = {**(job.payload or {}), maiu_client.JOB_TARGET_KEY: pinned}
            if job.external_ref:
                # 이미 등록한 에셋이 있으면 다시 등록하지 않고 import 상태 폴링으로 넘긴다.
                # 분석 요청(`dispatch`)은 요청 시점에 에셋을 등록한다.
                # 그래서 대기 단계에 에셋 번호가 있으면 MI에 에셋이 이미 있다.
                # 다시 등록하면 에셋이 둘 생기고 첫 에셋은 고아가 된다.
                _advance_ok(job, "registering")
                job.in_flight_count += 1
                count += 1
                continue
            video = videos.get(job.video_id)
            # 분석 입력은 매체 결정자가 고른다.
            # dispatch와 같은 함수를 써서 같은 규칙을 적용한다.
            uri = resolver.media_uri(video, "analysis_input") if video else None
            uri = uri or f"fake://{job.video_id}"
            try:
                client = await maiu_client.client_for_job(db, job)
                asset_id = await client.register_asset(video_uri=uri)
                job.external_ref = asset_id
                _advance_ok(job, "registering")
                job.in_flight_count += 1
                audit.emit(
                    db, event_type="maiu.stage.register_triggered", target_kind="maiu_job",
                    target_id=_job_target(job), project_id=job.project_id,
                    payload={"asset_id": asset_id, "video_id": str(job.video_id)},
                )
                count += 1
            except Exception as e:  # noqa: BLE001
                gate.note(job, e)
                await _on_stage_error(job, e, db=db)
    await db.commit()
    return count


# --- `registering` → `registered`: 단계 1b, import-status 폴링과 영상 메타 반영 ---

async def bulk_poll_registrations(db: AsyncSession) -> int:
    """registering 행의 import-status를 폴링해 registered 또는 실패로 옮긴다.

    처리 건수를 돌려준다.
    """
    jobs = await _lock_batch(db, stage="registering")
    if not jobs:
        return 0
    video_ids = [j.video_id for j in jobs]
    videos = {
        v.video_id: v for v in (
            await db.scalars(select(Video).where(Video.video_id.in_(video_ids)))
        )
    }
    count = 0
    gate = ThrottleGate()
    for job in jobs:
        if gate.blocked(job):
            continue
        # 잡마다 행을 다시 잠그고 그 잡을 끝내면 바로 커밋한다(`_job_tx`).
        async with _job_tx(db):
            if await _relock(db, job, stage="registering") is None:
                continue
            if not job.external_ref:
                await _on_stage_error(job, RuntimeError("no asset_id"), db=db)
                continue
            try:
                client = await maiu_client.client_for_job(db, job)
                raw = await client.get_import_status(job.external_ref)
                status = (raw.get("uploadStatus") or "").upper()
                # 상태 판정은 클라이언트의 `classify_stage_status` 한 곳에서 한다.
                # 여기에 상태 값 목록을 따로 두면 두 목록이 달라진다.
                # 예를 들어 `FAILED`가 빠지면 실패한 에셋을 계속 '진행 중'으로 처리한다.
                verdict = classify_stage_status(
                    status, ok=_UPLOAD_OK_SET, fail=_UPLOAD_FAIL_SET, pending=_UPLOAD_PENDING)
                if verdict == "ok":
                    _advance_ok(job, "registered")
                    # 분석본과 원본 중 무엇을 등록했는지 기록한다.
                    # 게이트 항목은 보낼 조건을 갖췄는지만 표시한다.
                    # MI가 실제로 분석하는 파일은 이 기록에만 남는다.
                    v = videos.get(job.video_id)
                    await media_stage_runs.finish(
                        db, job.video_id, "register", ok=True,
                        detail={"registered": "analysis" if (v and v.analysis_uri) else "original"},
                    )
                    job.in_flight_count = max(0, job.in_flight_count - 1)
                    await _apply_import_metadata(db, videos.get(job.video_id), job, raw)
                    audit.emit(
                        db, event_type="maiu.stage.registered", target_kind="maiu_job",
                        target_id=_job_target(job), project_id=job.project_id,
                        payload={"asset_id": job.external_ref, "uploadStatus": status},
                    )
                    count += 1
                elif verdict == "failed":
                    # MI가 돌려준 사유 문장(`uploadResult`)을 그대로 싣는다.
                    # 예를 들어 "지원하지 않는 형식입니다" 같은 문장이다.
                    # 화면의 '막힘' 사유로 "MI 분석 실패"보다 구체적이다.
                    await _on_stage_error(
                        job,
                        RuntimeError(
                            f"업로드 실패({status}): {raw.get('uploadResult') or '사유 미제공'}"
                        ),
                        db=db,
                    )
                else:
                    # 진행 중이다. 폴링 시각을 갱신하고 다음 조회 시각을 정한다.
                    _schedule_next_poll(job)
            except Exception as e:  # noqa: BLE001
                gate.note(job, e)
                await _on_stage_error(job, e, db=db)
    await db.commit()
    return count


# --- `registered` → `analyzing`: 단계 2, 분석 요청 ----------------------

async def bulk_check_analyses(db: AsyncSession) -> int:
    """registered 행의 분석을 요청하고 analyzing으로 옮긴다. 처리 건수를 돌려준다."""
    jobs = await _lock_batch(db, stage="registered")
    if not jobs:
        return 0
    count = 0
    gate = ThrottleGate()
    for job in jobs:
        if gate.blocked(job):
            continue
        # 잡마다 행을 다시 잠그고 그 잡을 끝내면 바로 커밋한다(`_job_tx`).
        async with _job_tx(db):
            if await _relock(db, job, stage="registered") is None:
                continue
            if not job.external_ref:
                await _on_stage_error(job, RuntimeError("no asset_id"), db=db)
                continue
            try:
                client = await maiu_client.client_for_job(db, job)
                # 장면 옵션은 요청 시점에 잡에 기록한 값으로 보낸다.
                # 현재 프로젝트 설정을 다시 읽으면 대기 중에 설정이 바뀐 잡이
                # 운영자가 고른 값과 다르게 분석된다.
                opts = (job.payload or {}).get("scene_options")
                analysis_id = await client.submit_analysis(
                    job.external_ref,
                    scene_options.SceneOptions(**opts).to_maiu_body() if opts else None,
                )
                job.payload = {**(job.payload or {}), "maiu_analysis_id": analysis_id}
                _advance_ok(job, "analyzing")
                await media_stage_runs.start(db, job.video_id, "analyze")
                job.in_flight_count += 1
                audit.emit(
                    db, event_type="maiu.stage.analyze_triggered", target_kind="maiu_job",
                    target_id=_job_target(job), project_id=job.project_id,
                    payload={"analysis_id": analysis_id},
                )
                count += 1
            except Exception as e:  # noqa: BLE001
                gate.note(job, e)
                await _on_stage_error(job, e, db=db)
    await db.commit()
    return count


# --- `analyzing` → `analyzed`: 단계 2b, analyze-status 폴링 -------------

async def bulk_poll_analyses(db: AsyncSession) -> int:
    """analyzing 행의 분석 상태를 폴링해 analyzed 또는 실패로 옮긴다.

    처리 건수를 돌려준다.
    """
    jobs = await _lock_batch(db, stage="analyzing")
    if not jobs:
        return 0
    count = 0
    gate = ThrottleGate()
    for job in jobs:
        if gate.blocked(job):
            continue
        # 잡마다 행을 다시 잠그고 그 잡을 끝내면 바로 커밋한다(`_job_tx`).
        async with _job_tx(db):
            if await _relock(db, job, stage="analyzing") is None:
                continue
            if not job.external_ref:
                await _on_stage_error(job, RuntimeError("no asset_id"), db=db)
                continue
            try:
                client = await maiu_client.client_for_job(db, job)
                raw = await client.get_analyze_status(job.external_ref)
                status = (raw.get("status") or "").upper()
                # 상태 판정은 클라이언트 한 곳에서 한다.
                # import 단계와 같은 이유로 상태 값 목록을 복제하지 않는다.
                verdict = classify_stage_status(
                    status, ok=_ANALYZE_OK_SET, fail=_ANALYZE_FAIL_SET, pending=_ANALYZE_PENDING)
                if verdict == "ok":
                    # analysis_id 쌍에 indexId를 채운다.
                    # 그러면 뒤의 get_summary가 지연 조회를 건너뛴다.
                    index_id = raw.get("indexId")
                    if index_id is not None:
                        job.payload = {**(job.payload or {}),
                                       "maiu_analysis_id": f"{job.external_ref}:{index_id}"}
                    _advance_ok(job, "analyzed")
                    await media_stage_runs.finish(db, job.video_id, "analyze", ok=True)
                    job.in_flight_count = max(0, job.in_flight_count - 1)
                    audit.emit(
                        db, event_type="maiu.stage.analyzed", target_kind="maiu_job",
                        target_id=_job_target(job), project_id=job.project_id,
                        payload={"analysis_id": _analysis_id(job), "indexId": index_id},
                    )
                    count += 1
                elif verdict == "failed":
                    msg = raw.get("analysisStatusMessage") or status
                    # 재요청 전에 NCP 쪽 분석 상태를 정리한다.
                    # 이미 정리됐을 수 있어 취소 실패는 무시한다.
                    import contextlib
                    with contextlib.suppress(Exception):
                        await client.cancel_analysis(job.external_ref)
                    await _on_stage_error(job, RuntimeError(f"analyze failed: {msg}"), db=db)
                else:
                    # 진행 중이다. 폴링 시각을 갱신하고 다음 조회 시각을 정한다.
                    _schedule_next_poll(job)
            except Exception as e:  # noqa: BLE001
                gate.note(job, e)
                await _on_stage_error(job, e, db=db)
    await db.commit()
    return count


# --- `analyzed` → `summarizing`: 단계 3, clips 생성과 장면 요약 요청 -------

async def bulk_check_summaries(db: AsyncSession) -> int:
    """analyzed 행으로 장면 단위 clips와 work_item을 만들고 장면 요약을 요청한다.

    stage는 summarizing이 된다. 처리 건수를 돌려준다.
    clips는 바로 라벨링 큐에 들어간다. 장면 요약(VLM)은 완료 뒤
    `bulk_poll_scene_summaries`가 auto_caption.summary에 붙인다.
    분석 결과 조회가 실패하면 단계를 analyzed로 유지하고 다음 사이클에 다시 조회한다.
    요약 요청 자체가 실패하면 clips는 그대로 두고 done으로 마감한다.
    이미 라벨링 큐에 들어간 클립은 회수하지 않는다. 영상은 단계 이력의 요약 실패로 막힘이 되고
    요약 재요청을 기다린다.
    """
    jobs = await _lock_batch(db, stage="analyzed")
    if not jobs:
        return 0
    count = 0
    gate = ThrottleGate()
    for job in jobs:
        if gate.blocked(job):
            continue
        # 잡마다 행을 다시 잠그고 그 잡을 끝내면 바로 커밋한다(`_job_tx`).
        async with _job_tx(db):
            if await _relock(db, job, stage="analyzed") is None:
                continue
            if not _analysis_id(job):
                await _on_stage_error(job, RuntimeError("no analysis_id"), db=db)
                continue
            # 분석 결과를 받은 뒤에 단계를 옮긴다. 먼저 summarizing으로 옮기면 결과 조회가
            # 일시 오류로 실패한 잡이 요약 요청 번호 없이 summarizing 단계에 남는다.
            try:
                client = await maiu_client.client_for_job(db, job)
                summary = await client.get_summary(_analysis_id(job))
            except Exception as e:  # noqa: BLE001
                gate.note(job, e)
                await _on_stage_error(job, e, db=db)
                continue
            _advance_ok(job, "summarizing")
            await media_stage_runs.start(db, job.video_id, "summarize")
            job.in_flight_count += 1
            try:
                await _create_clips_from_summary(db, job, summary)
                # clip별 work_item을 만든다(cleared·auto_cleared·cleared_with_edit).
                await db.flush()  # 새 clip의 PK를 받는다
                from api_gateway.services.work import service as work_service
                video_clips = (await db.scalars(
                    select(Clip).where(Clip.video_id == job.video_id)
                )).all()
                for c in video_clips:
                    await work_service.ensure_for_clip(db, c)
                payload = {**job.payload, "summary_keys": sorted(summary.keys())}
                try:
                    sid = await client.request_scene_summary(_analysis_id(job))
                    payload["scene_summary_id"] = sid
                    job.payload = payload
                    audit.emit(
                        db, event_type="maiu.stage.summarize_requested", target_kind="maiu_job",
                        target_id=_job_target(job), project_id=job.project_id,
                        payload={"scene_summary_id": sid, "clip_count": len(video_clips)},
                    )
                except Exception as se:  # noqa: BLE001 — 요약 요청 실패는 clips를 막지 않음
                    job.payload = payload
                    _advance_ok(job, "done")
                    # 요약 요청 자체가 실패했다. summarize 시작 기록이 남아 있으므로 실패로 닫는다.
                    await media_stage_runs.finish(
                        db, job.video_id, "summarize", ok=False,
                        error=error_text.operator_reason(se)[:400])
                    job.in_flight_count = max(0, job.in_flight_count - 1)
                    audit.emit(
                        db, event_type="maiu.stage.summarize_request_failed",
                        target_kind="maiu_job",
                        target_id=_job_target(job), project_id=job.project_id,
                        payload={"clip_count": len(video_clips), "error": str(se)[:200]},
                    )
                count += 1
            except Exception as e:  # noqa: BLE001
                await _on_stage_error(job, e, db=db)
    await db.commit()
    return count


# --- `summarizing` → `done`: 단계 3b, 장면 요약 폴링과 clip 부착 ---------

async def bulk_poll_scene_summaries(db: AsyncSession) -> int:
    """summarizing 행의 장면 요약을 폴링하고, 끝나면 clips에 요약을 붙인다.

    처리 건수를 돌려준다.
    SUMMARIZE_COMPLETED면 get_scene_summaries로 받은 scene별 description을
    같은 scene clip의 auto_caption.summary에 쓴다.
    요약을 붙이면 done이 된다.
    요약 실패(SUMMARIZE_FAIL)면 clips를 그대로 두고 done으로 마감한다.
    영상은 단계 이력의 요약 실패로 막힘이 된다.
    요약 재요청으로 돌아온 잡도 같은 경로로 요약을 붙인다.
    진행 중이면 stage를 유지하고 다음 사이클에 다시 폴링한다.
    """
    jobs = await _lock_batch(db, stage="summarizing")
    if not jobs:
        return 0
    count = 0
    gate = ThrottleGate()
    for job in jobs:
        if gate.blocked(job):
            continue
        # 잡마다 행을 다시 잠그고 그 잡을 끝내면 바로 커밋한다(`_job_tx`).
        async with _job_tx(db):
            if await _relock(db, job, stage="summarizing") is None:
                continue
            sid = (job.payload or {}).get("scene_summary_id")
            if not sid and not await _has_clips(db, job):
                # 요약 요청 번호도 클립도 없으면 분석 결과를 반영하지 못한 잡이다.
                # done으로 닫으면 라벨링할 클립 없이 분석이 끝난 것으로 보이므로 analyzed로 되돌려
                # 다음 사이클에 결과를 다시 받게 한다.
                _advance_ok(job, "analyzed")
                await media_stage_runs.finish(
                    db, job.video_id, "summarize", ok=False,
                    error=_SUMMARY_REOPENED_TEXT)
                job.in_flight_count = max(0, job.in_flight_count - 1)
                count += 1
                continue
            if not sid:
                # 클립은 만들었지만 요약 요청이 빠진 job이다. 요약 없이 done으로 마감하고
                # 단계 이력에 요약 실패를 남긴다. 영상은 요약 재요청 전까지 막힘이다.
                _advance_ok(job, "done")
                # 이 사유는 준비 타임라인에 그대로 표시되는 화면 문구라서 영문 낱말을 쓰지 않는다.
                await media_stage_runs.finish(
                    db, job.video_id, "summarize", ok=False,
                    error=error_text.SUMMARY_SKIPPED_TEXT)
                job.in_flight_count = max(0, job.in_flight_count - 1)
                count += 1
                continue
            try:
                client = await maiu_client.client_for_job(db, job)
                status = await client.check_scene_summary_status(_analysis_id(job), sid)
                # 판정은 `classify_stage_status` 한 곳에서 한다. 정확한 집합만 비교하면
                # `FAILED`·`CANCELED` 같은 값이 진행 중으로 남아 잡이 경고 없이 계속 폴링된다.
                verdict = classify_stage_status(
                    status, ok=_SUMMARY_OK, fail=_SUMMARY_FAIL, pending=_SUMMARY_PENDING)
                if verdict == "ok":
                    summaries = await client.get_scene_summaries(_analysis_id(job))
                    updated = await _attach_scene_summaries(db, job, summaries)
                    audit.emit(
                        db, event_type="maiu.stage.summarized", target_kind="maiu_job",
                        target_id=_job_target(job), project_id=job.project_id,
                        payload={"scene_count": len(summaries), "clips_updated": updated},
                    )
                    # 요약이 최종 결과이므로 done으로 옮긴다.
                    _advance_ok(job, "done")
                    await media_stage_runs.finish(db, job.video_id, "summarize", ok=True)
                    job.in_flight_count = max(0, job.in_flight_count - 1)
                    count += 1
                elif verdict == "failed":
                    _advance_ok(job, "done")
                    await media_stage_runs.finish(
                        db, job.video_id, "summarize", ok=False,
                        error=error_text.SUMMARY_FAILED_TEXT)
                    job.in_flight_count = max(0, job.in_flight_count - 1)
                    audit.emit(
                        db, event_type="maiu.stage.summarize_failed", target_kind="maiu_job",
                        target_id=_job_target(job), project_id=job.project_id,
                        payload={"status": status},
                    )
                    count += 1
                else:
                    # 진행 중이다. 폴링 시각을 갱신하고 다음 조회 시각을 정한다.
                    _schedule_next_poll(job)
            except Exception as e:  # noqa: BLE001
                gate.note(job, e)
                await _on_stage_error(job, e, db=db)
    await db.commit()
    return count


# --- 단계 4: 실패가 `_MAX_RETRY`회 쌓인 잡을 deferred로 승격 ---------------

async def promote_failures_to_deferred(db: AsyncSession) -> int:
    """failed이고 `attempt`(DTO의 retry_count)가 `_MAX_RETRY` 이상인 잡을 deferred로 승격한다.

    승격한 잡은 보류 사이클(10:1 배분)에서 다시 처리된다. 처리 건수를 돌려준다.
    """
    rows = (await db.scalars(
        select(AnalysisJob).where(
            AnalysisJob.stage == "failed",
            AnalysisJob.adapter_key == MAIU_KEY,
            AnalysisJob.attempt >= _MAX_RETRY,
            AnalysisJob.deferred.is_(False),
        ).with_for_update(skip_locked=True).limit(_BATCH)
    )).all()
    for job in rows:
        job.deferred = True
        audit.emit(
            db, event_type="maiu.stage.deferred", target_kind="maiu_job",
            target_id=_job_target(job), project_id=job.project_id,
            payload={"retry_count": job.attempt},
        )
    if rows:
        await db.commit()
    return len(rows)


async def sweep_stale_in_flight(db: AsyncSession) -> int:
    """`*ing` 단계에서 폴링이 임계 시간보다 오래 멈춘 job을 안전 상태로 되돌린다.

    워커가 비정상 종료돼 폴링이 끊기면 updated_at이 임계 시각보다 오래된다.
    이때 job을 `_resume_status`가 정한 재폴링 지점으로 되돌린다.
    pending으로 되돌리면 register_asset·submit_analysis를 반복해 중복 에셋과 재분석 과금이 생긴다.
    긴 영상은 분석 시간이 stale 임계를 넘는 것이 정상이다.
    진행 중인 정상 폴링은 사이클마다 updated_at을 갱신하므로 여기에 걸리지 않는다.

    임계값은 `settings.maiu_sweep_stale_minutes`(기본값 10)다.
    """
    threshold = datetime.now(UTC) - timedelta(minutes=settings.maiu_sweep_stale_minutes)
    rows = (await db.scalars(
        select(AnalysisJob).where(
            AnalysisJob.stage.in_(("registering", "analyzing", "summarizing")),
            AnalysisJob.adapter_key == MAIU_KEY,
            AnalysisJob.in_flight_count > 0,
            AnalysisJob.updated_at < threshold,
            AnalysisJob.deferred.is_(False),
        ).with_for_update(skip_locked=True).limit(_BATCH)
    )).all()
    for job in rows:
        resume = _resume_status(job)
        audit.emit(
            db, event_type="maiu.stage.swept_stale", target_kind="maiu_job",
            target_id=_job_target(job), project_id=job.project_id,
            payload={"prev_stage": job.stage, "resume_status": resume,
                     "in_flight_count": job.in_flight_count},
        )
        # 스윕한 잡은 deferred로 보내지 않고 `_resume_status`가 정한 지점에서 정상 폴링을 잇는다.
        job_state.advance(job, resume)
        job.in_flight_count = 0
    if rows:
        await db.commit()
    return len(rows)


async def retry_deferred(db: AsyncSession, limit: int = 5) -> int:
    """보류(deferred) 잡을 안전 상태로 되돌려 다시 처리하게 한다.

    호출마다 오래된 순으로 최대 `limit`건을 처리한다. 처리 건수를 돌려준다.
    `_resume_status`가 정한 지점으로 되돌려 재등록·재분석 과금을 막는다.
    `attempt`(DTO의 retry_count)와 `error_msg`는 초기화한다.
    `run_once`의 보류 사이클(`slot_counter`의 10:1 배분)에서 불린다.
    """
    rows = (await db.scalars(
        select(AnalysisJob).where(AnalysisJob.deferred.is_(True))
        .order_by(AnalysisJob.updated_at)
        .with_for_update(skip_locked=True).limit(limit)
    )).all()
    for job in rows:
        job_state.advance(job, _resume_status(job))
        job.deferred = False
        job.attempt = 0
        job.error_msg = None
        audit.emit(
            db, event_type="maiu.stage.retry_deferred", target_kind="maiu_job",
            target_id=_job_target(job), project_id=job.project_id,
        )
    if rows:
        await db.commit()
    return len(rows)


# --- 내부 헬퍼 -----------------------------------------------------------

def _analysis_id(job) -> str | None:
    """`payload`에 저장된 MI 분석 id를 돌려준다.

    계약에 벤더 전용 필드를 두지 않으므로 분석 id는 `payload`에 저장한다.
    `external_ref`에는 에셋 id 하나만 들어간다.
    읽는 곳이 여럿이라 함수로 둔다.
    곳곳에서 `payload.get(...)`으로 읽으면 키 오타가 오류 없이 `None`이 된다.
    """
    return (job.payload or {}).get("maiu_analysis_id")


def _resume_status(job: AnalysisJob) -> str:
    """재시도와 스윕 때 되돌릴 안전 상태를 정한다. 이미 요청한 비싼 외부 호출을 반복하지 않는다.

    register_asset(등록)을 반복하면 중복 에셋이, submit_analysis(분석)를 반복하면 재과금이 생긴다.
    그래서 이미 끝낸 외부 호출의 다음 단계로 되돌린다.
    analysis_id가 있으면 분석을 이미 요청했으므로 analyzing으로 되돌린다.
    그러면 analyze-status만 다시 폴링한다.
    asset_id만 있으면 등록만 끝났으므로 registered로 되돌려 분석을 한 번 요청한다.
    둘 다 없으면 pending으로 되돌려 처음부터 등록한다.
    """
    if _analysis_id(job):
        return "analyzing"
    if job.external_ref:
        return "registered"
    return "pending"


async def _lock_batch(db: AsyncSession, *, stage: str) -> Sequence[AnalysisJob]:
    """주어진 stage의 보류되지 않은 job을 FOR UPDATE SKIP LOCKED로 최대 `_BATCH`건 고른다.

    백오프 중인 job과 다음 조회 시각이 오지 않은 job은 뺀다.
    일시 오류(429 등)를 받은 job은 `payload.retry_after`가 지날 때까지 건너뛴다.
    이 조건이 없으면 5초마다 같은 job을 호출해 요청량 제한이 풀리지 않는다.
    그러면 단계가 넘어가지 못한다.
    진행 중인 job은 `payload.next_poll_at`이 지날 때까지 건너뛴다(`_schedule_next_poll`).
    고른 행의 잠금은 첫 잡을 커밋할 때 풀린다. 단계 함수가 잡마다 `_relock`으로 다시 잠근다.
    """
    now = datetime.now(UTC)
    return (await db.scalars(
        select(AnalysisJob)
        .where(
            # 단계(`stage`)로 고른다. `status`는 외부에 보여 주는 중립 상태라서 여러 단계가
            # `running` 하나로 합쳐진다.
            # 그래서 `status`로는 `registered`와 `analyzed`를 구분할 수 없다.
            AnalysisJob.stage == stage,
            # MI 잡만 고른다.
            # 이 조건이 없으면 스택이 둘 이상일 때 MI reconciler가 다른 스택의 잡을 가져간다.
            AnalysisJob.adapter_key == MAIU_KEY,
            AnalysisJob.deferred.is_(False),
            or_(
                AnalysisJob.payload["retry_after"].astext.is_(None),
                AnalysisJob.payload["retry_after"].astext <= now.isoformat(),
            ),
            or_(
                AnalysisJob.payload[_NEXT_POLL_KEY].astext.is_(None),
                AnalysisJob.payload[_NEXT_POLL_KEY].astext <= now.isoformat(),
            ),
        )
        .order_by(AnalysisJob.updated_at)
        .limit(_BATCH)
        .with_for_update(skip_locked=True)
    )).all()


async def _relock(db: AsyncSession, job: AnalysisJob, *, stage: str) -> AnalysisJob | None:
    """잡 행을 다시 잠그고 DB의 현재 값으로 고친다. 그사이 단계가 바뀌었거나 잠겨 있으면 None이다.

    배치를 고른 뒤 앞 잡을 커밋하면 배치 전체의 행 잠금이 풀린다. 그동안 화면의 재시도·취소가
    같은 행을 바꿀 수 있어 그 값을 읽지 않고 쓰면 사람의 조작을 덮어쓴다.
    """
    return await db.scalar(
        select(AnalysisJob)
        .where(AnalysisJob.id == job.id, AnalysisJob.stage == stage,
               AnalysisJob.deferred.is_(False))
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )


@asynccontextmanager
async def _job_tx(db: AsyncSession) -> AsyncIterator[None]:
    """잡 하나의 처리를 한 트랜잭션으로 묶는다. 정상으로 끝나면 커밋하고 예외면 되돌린다.

    외부 호출이 돌려준 id를 그 잡을 끝내는 즉시 커밋한다. 배치를 한 번에 커밋하면 뒤 잡에서
    워커가 비정상 종료될 때 앞 잡이 받은 에셋 id까지 사라져 다음 사이클이 다시 등록한다.
    """
    try:
        yield
    except BaseException:
        await db.rollback()
        raise
    await db.commit()
