"""영상 준비 타임라인 조립."""
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import PrepTimelineDTO, PrepTimelineEntry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import AnalysisJob, Clip, Project, User, Video
from api_gateway.services import authz, error_text
from api_gateway.services.deident_gap import (
    DEIDENT_BEFORE_ON,
    DEIDENT_OFF,
    deident_gap,
    reached_deident,
)


async def prep_timeline(db: AsyncSession, video_id: UUID, user: User) -> PrepTimelineDTO:
    """준비 타임라인을 만든다. 단계별 현재 상태, 실패·건너뜀 사유, 재시도 가능 여부를 담는다.

    라벨러를 뺀 역할만 볼 수 있다.
    단계는 프로젝트 분석 방식에 있는 단계만 싣는다. MI를 쓰지 않는 방식에는 분석 등록·분석·
    장면 요약(`_ANALYSIS_STAGES`)이 없다.
    단계 이력에 행이 있는 단계는 최신 시도의 상태와 실패 사유를 쓰고,
    행이 없는 단계는 현재 상태 열에서 파생한다.
    시각·소요·시도 수는 싣지 않는다. 단계 이력은 파이프라인 판정과 감사 기록이 읽는다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            볼 권한이 없는 경우(403 `not_work_result_viewer`).
    """
    from api_gateway.services.media import stage_runs
    from api_gateway.services.media.transcode import plan_outputs

    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    # 화면에서만 막으면 주소로 직접 호출할 수 있어서 서버에서 막는다.
    await authz.require_work_result_viewer(db, video.project_id, user)

    # 어느 단계에서 멈췄는지 보이도록 중립 상태 대신 MI 단계를 그대로 표시한다.
    # `analysis_job`은 중립 테이블이라 한 영상에 여러 행(MI, 씬 분할)이 있을 수 있어
    # 어댑터 조건을 건다. 조건이 없으면 씬 분할 행을 MI 단계로 잘못 표시한다.
    from api_gateway.services.analysis.registry import MAIU_KEY
    from api_gateway.services.media import scene_split as scene_split_service

    maiu = await db.scalar(
        select(AnalysisJob).where(
            AnalysisJob.video_id == video_id, AnalysisJob.adapter_key == MAIU_KEY
        ).limit(1)
    )
    # 씬 분할을 직접 하는 스택의 잡. 이 행이 있으면 씬 분할 단계의 상태는 이 행으로 정한다.
    # MI 스택에는 이 행이 없어서 씬 분할 단계를 건너뜀과 사유로 표시한다.
    split = await db.scalar(
        select(AnalysisJob).where(
            AnalysisJob.video_id == video_id,
            AnalysisJob.adapter_key == scene_split_service.ADAPTER_KEY,
        ).limit(1)
    )
    runs = await stage_runs.latest_runs(db, video_id)
    # 비식별화 문구는 지금 이 프로젝트가 비식별화를 쓰는지에 따라 달라진다(아래 `derived`).
    proj = (await db.execute(
        select(Project.deident_enabled, Project.settings)
        .where(Project.project_id == video.project_id)
    )).first()
    deident_on = bool(proj and proj.deident_enabled)
    # 분석 서비스(MI)를 쓰지 않는 방식(샷 검출)에는 분석 등록·분석·장면 요약 단계가 없다.
    # 그 단계를 표시하면 실행되지 않을 단계를 기다리는 것처럼 보인다.
    from api_gateway.services.analysis import presets
    mi_stages = presets.needs_analysis(presets.of_project(proj.settings if proj else None))
    now = datetime.now(UTC)
    has_clips = bool(await db.scalar(
        select(Clip.clip_id).where(Clip.video_id == video_id).limit(1)
    ))

    # 장면 요약을 받지 못한 영상이면 True. 분류 축과 같은 조건(`_summary_blocked_expr`)을 쓴다.
    from api_gateway.services.media.video.classify import _summary_blocked_expr
    summary_blocked = bool(await db.scalar(
        select(_summary_blocked_expr()).select_from(Video).where(Video.video_id == video_id)
    ))

    probe_meta = (video.extra or {}).get("probe") or {}
    tc_error = (video.extra or {}).get("transcode_error")
    # 건너뛴 사유는 파이프라인이 실제로 쓰는 계획(`plan_outputs`)에서 파생한다.
    # `needs_transcode`는 사본이 하나라도 필요한지 판정하고,
    # transcode 단계는 분석본이 필요한지 판정한다(`pipeline._needs`).
    # `h264 · 720p` 원본은 재생본만 필요해서 두 판정이 다르므로 `needs_transcode`를 쓰지 않는다.
    plan = plan_outputs(probe_meta, video.transcode_profile)

    def derived(stage: str) -> tuple[str, str | None, str | None]:
        """행이 없을 때 현재 상태에서 (status, error, skipped_reason)을 파생한다.

        실패 사유는 `error_text.operator_text`를 거친다. 분석 단계(등록·분석)는
        `error_text.analysis_error_text`를 거쳐 주어가 있는 문장(`분석하지 못했습니다`)이 된다.
        내부 경로·HTTP 본문 같은 원문은 화면에 싣지 않는다.
        사유 정보가 없으면 `None`이다. 상태(`실패`)를 사유 필드에서 되풀이하지 않는다.
        """
        # 중립 `status`는 `running` 하나로 합쳐져 `registered`와 `analyzed`를 구분할 수 없다.
        # 그래서 진행 위치를 표시하는 이 함수는 `stage`를 읽는다. 판정은 `status`로 한다.
        m = maiu.stage if maiu else None
        if stage == "upload":
            if video.upload_status == "uploaded":
                return "ok", None, None
            if video.upload_status == "pending":
                # 전송 시작 여부(`mpu_upload_id`)로 `running`과 `pending`을 가른다.
                # 영상 행은 파일보다 먼저 생기므로
                # 멀티파트를 열기 전에 창을 닫으면 `pending`으로 남는다.
                # 이 행을 `running`으로 표시하면 전송한 바이트가 없는데 전송 중으로 보인다.
                started = bool((video.extra or {}).get("mpu_upload_id"))
                return ("running" if started else "pending"), None, None
            # 여기에 도달하는 값은 `failed` 하나다.
            # 사유 정보가 없어 비워 두고 상태만 `실패`로 표시한다.
            return "failed", None, None
        if stage == "probe":
            if video.probe_status == "passed":
                return "ok", None, None
            if video.probe_status == "pending":
                up = video.upload_status == "uploaded"
                return ("running", None, None) if up else ("pending", None, None)
            if video.probe_status == "rejected":
                # 위반 코드는 `규격 위반: a; b` 형식으로 싣고 화면이 문장으로 바꾼다
                # (`video_detail.violationLabel`). 위반 코드가 없으면 사유를 비운다.
                v = probe_meta.get("violations")
                return "failed", ("규격 위반: " + "; ".join(v)) if v else None, None
            return "failed", (
                error_text.operator_text(probe_meta.get("error"))
                or "원본을 읽지 못해 검증하지 못했습니다."
            ), None
        if stage == "transcode":
            st = video.transcode_status
            if st == "skipped":
                if video.probe_status != "passed" and not (m or has_clips):
                    return "pending", None, None   # 검증 전이라 필요 여부를 아직 판정할 수 없다
                if video.transcode_profile == "never":
                    return "skipped", None, "원본을 그대로 쓰도록 설정해 변환하지 않았습니다."
                if "analysis" in plan:
                    # 건너뛴 뒤 재검사로 규격 판정이 바뀌어 기록(건너뜀)과 지금 계획이 다르다.
                    # 어느 판정이 맞는지는 표시하지 않고 바뀐 사실만 적는다.
                    return "skipped", None, (
                        "건너뛴 뒤 규격 판정이 바뀌었습니다. 지금 기준으로는 분석본이 필요합니다.")
                # transcode 단계는 분석본만 만든다.
                # 재생본만 필요한 영상은 확정(publish) 단계가 재생본을 만든다.
                # 이 경우를 "변환 안 함"이라고 쓰면 재생본도 만들지 않는 것처럼 읽힌다.
                if "playback" in plan:
                    return "skipped", None, "원본이 이미 분석 규격이라 분석본을 만들지 않았습니다."
                return "skipped", None, "원본이 이미 분석·재생 규격이라 만들 사본이 없습니다."
            if st in ("pending", "queued", "running"):
                return "running", None, None
            if st == "done":
                return "ok", None, None
            return "failed", error_text.operator_text(tc_error), None
        if stage == "deident":
            # `skipped`(쓰지 않음)와 `failed`(처리하지 못함)를 나눠 정상과 장애를 구분한다.
            # 품질 검사와 같은 문구를 쓰도록 판정은 `deident_gap` 한 곳에 둔다.
            # 비식별화를 켜기 전에 처리된 영상은 `needed`다.
            # 이 영상은 비식별화를 해야 분석으로 넘어가므로(`classify._unmasked_expr`)
            # `skipped`로 두면 진행을 막는 단계가 쓰지 않는 단계처럼 보인다.
            # 사유 문장은 세 번째 값으로 넘기고 아래 조립에서 `note`에 싣는다.
            reached = reached_deident(
                probe_status=video.probe_status, transcode_status=video.transcode_status,
                publish_status=video.publish_status, downstream=bool(m or has_clips),
            )
            gap = deident_gap(video.deident_status, deident_on, reached=reached)
            if gap == "off":
                return "skipped", None, DEIDENT_OFF
            if gap == "before_on":
                return "needed", None, DEIDENT_BEFORE_ON
            if gap == "waiting":
                # 규격 검증 전이나 변환 중인 영상은 비식별화 단계가 아직 열리지 않아 대기로 그린다.
                return "pending", None, None
            if gap == "running":
                return "running", None, None
            if gap is None:
                return "ok", None, None
            deident_error = (video.extra or {}).get("deident_error")
            return "failed", error_text.operator_text(deident_error), None
        if stage == "publish":
            st = video.publish_status
            if st == "skipped":
                if video.probe_status != "passed" and not (m or has_clips):
                    return "pending", None, None   # 검증 전이라 필요 여부를 아직 판정할 수 없다
                # publish는 계획(`plan`)이 비었고 비식별화도 꺼졌을 때만 건너뛴다
                # (`pipeline._needs`).
                # 설정 때문에 건너뛴 경우와 필요가 없어 건너뛴 경우의 사유를 나눈다.
                if video.transcode_profile == "never":
                    return "skipped", None, (
                        "원본을 그대로 쓰도록 설정해 재생본을 만들지 않았습니다.")
                # 앞 단계(분석본 변환·비식별화)가 끝나지 않았거나 실패했으면
                # 이 단계는 열리지 않았고, `skipped`는 열 기본값일 뿐 실제 상태는 대기다.
                # 규격 사유 문장을 쓰면 변환이 필요한 영상을 규격에 맞는 영상으로 잘못 설명한다.
                upstream = (video.transcode_status, video.deident_status)
                if any(u in ("pending", "queued", "running", "failed") for u in upstream):
                    return "pending", None, None
                return "skipped", None, (
                    "원본이 이미 재생 규격에 맞아 재생본을 따로 만들지 않았습니다.")
            if st in ("pending", "queued", "running"):
                return "running", None, None
            if st == "done":
                return "ok", None, None
            publish_error = (video.extra or {}).get("publish_error")
            return "failed", error_text.operator_text(publish_error), None
        # 씬 분할을 직접 하는 스택이면 그 잡의 상태로 이 단계를 정한다.
        # 이 스택에는 MI 행이 없어서 아래 MI 분기로 내려가면 모두 "분석 전"으로 표시된다.
        # 그래서 MI 분기보다 먼저 본다.
        if stage == "scene_split" and split is not None:
            if split.status == "pending":
                return "pending", None, None
            if split.status == "running":
                return "running", None, None
            if split.status == "failed":
                return "failed", error_text.operator_text(split.error_msg), None
            return "ok", None, None
        # MI 3단계는 `analysis_job`의 MI 행에서 파생한다.
        # 벤더 상태는 실패한 단계를 따로 남기지 않아서 failed는 analyze 행에 싣는다.
        # prep_blocked_stage='analysis'와 같은 방식이다.
        if not m:
            return ("ok", None, None) if has_clips else ("pending", None, None)
        if stage == "register":
            if m in ("pending", "registering"):
                return "running", None, None
            if m == "failed":
                # 등록 완료 여부는 자산 핸들(`external_ref`, MI 자산 번호)로 판정한다.
                # 핸들이 있으면 분석에서 멈춘 영상이고, 없으면 등록에서 멈춘 영상이다.
                # 핸들이 없을 때 등록 단계를 `대기`로 두면
                # 등록도 안 된 영상이 분석에 실패한 것처럼 읽힌다.
                if maiu.external_ref:
                    return "ok", None, None
                return "failed", error_text.analysis_error_text(maiu.error_msg), None
            return "ok", None, None
        if stage == "scene_split":
            # MI 스택은 분석하면서 장면을 함께 나누므로 이 단계를 건너뜀과 사유로 표시한다.
            # 샷 검출 스택이면 이 단계가 실제로 실행되고, 위의 분기가 그 잡의 상태를 읽는다.
            if m in ("pending", "registering", "registered"):
                return "pending", None, None
            return "skipped", None, "분석 단계에서 장면을 함께 나눕니다."
        if stage == "analyze":
            if m in ("pending", "registering", "registered"):
                return "pending", None, None
            if m in ("analyzing",):
                return "running", None, None
            if m == "failed":
                # 자산 핸들이 없으면 등록에서 멈춰 분석을 시작하지 않았으므로 대기로 표시한다.
                if not maiu.external_ref:
                    return "pending", None, None
                return "failed", error_text.analysis_error_text(maiu.error_msg), None
            return "ok", None, None
        # summarize
        if m in ("summarizing",):
            return "running", None, None
        if m == "done":
            return "ok", None, None
        return "pending", None, None

    entries: list[tuple[PrepTimelineEntry, bool]] = []
    for stage in stage_runs.STAGE_ORDER:
        if stage in _ANALYSIS_STAGES and not mi_stages:
            continue
        run = runs.get(stage)
        d_status, d_error, d_skip = derived(stage)
        run_stale = run is not None and run.status == "running" \
            and d_status in ("ok", "failed", "skipped")
        # 다시 연 장면 분할 잡은 사유(`error_msg`) 없이 대기하고(`scene_split._reopen`),
        # 재시도·회수로 돌아온 대기 잡은 사유를 남긴다.
        # 다시 연 잡의 단계 기록은 다음 투입 전까지 실패로 남으므로 기록대로 그리면
        # 재시도를 누른 뒤에도 실패와 재시도 버튼이 그대로 보인다.
        reopened = (
            stage == "scene_split" and split is not None and split.status == "pending"
            and split.error_msg is None and run is not None and run.status == "failed"
        )
        # 비식별화 시도가 실패한 뒤 자동 재시도를 기다리는 영상이다
        # (일시 오류의 재시도 간격, 상한 전의 영구 오류, 회수).
        # 기록대로 `실패`로 그리면 다음 시도가 열릴 때마다 "진행 중"과 "실패"가 번갈아 보이고,
        # 그 줄의 재시도 버튼은 서버가 거절한다(`deident.retry`의 `deident_in_progress`).
        # 실패로 확정되면 상태 열이 `failed`가 되어 기록대로 `실패`로 그린다.
        auto_retry = (
            stage == "deident" and run is not None and run.status == "failed"
            and video.deident_status == "pending"
        )
        # 비식별화를 끈 뒤 대기에서 건너뜀으로 넘어간 영상이다(`deident.skip_disabled`).
        # 마지막 기록은 실패한 시도라서 쓰지 않기로 한 단계가 "실패"로 읽히지 않게
        # 판정(`deident_gap`)으로 그린다.
        dropped = (
            stage == "deident" and run is not None and run.status == "failed"
            and video.deident_status == "skipped"
        )
        # 현재 상태 열에서 파생한 줄이면 True. 아래의 앞 단계 대기 안내가 이 값을 본다.
        derived_row = False
        if auto_retry:
            reason = _readable_run_error(stage, run.error)
            entry = PrepTimelineEntry(
                stage=stage, status="pending",
                note=f"재시도 중 · {reason}" if reason else "재시도 중",
            )
        elif run is not None and not run_stale and not reopened and not dropped:
            # 기록을 우선한다.
            # 기록은 running인데 상태 열이 이미 끝났으면(회수, 경합) 파생 값을 쓴다.
            e_status = {"running": "running", "ok": "ok", "failed": "failed"}[run.status]
            entry = PrepTimelineEntry(
                stage=stage, status=e_status, error=_readable_run_error(stage, run.error),
            )
        else:
            # `needed`의 사유는 해야 할 일의 이유라서 `skipped_reason` 대신 `note`에 싣는다.
            needed = d_status == "needed"
            derived_row = True
            entry = PrepTimelineEntry(
                stage=stage, status=d_status, error=d_error,
                skipped_reason=None if needed else d_skip,
                note=d_skip if needed else None,
            )
        # 비식별화를 켜 놓고 가리지 않은 영상(위의 `needed`)은 다시 투입할 수 있다.
        # 화면은 `deident_enabled`를 받지 않으므로 서버가 정한다.
        if stage == "deident" and entry.status == "needed":
            entry.retryable = True
        # 장면 요약 실패 줄은 받을 재요청이 있을 때만 다시 넣을 수 있다.
        # 요약을 받지 못한 영상은 요약 재요청을, 요약 중 MI 잡이 실패한 영상은 분석 재요청을 받는다
        # (`maiu.service.retry_failed`).
        # 그 밖의 실패 기록(클립이 없는 영상, 결과를 다시 받는 중인 영상)은 재요청이 건너뛰므로
        # 버튼을 두지 않는다.
        if stage == "summarize" and entry.status == "failed":
            entry.retryable = summary_blocked or (maiu is not None and maiu.stage == "failed")
        # 비식별화를 끈 프로젝트에서 실패로 확정된 영상만 건너뛰기를 허용한다
        # (`deident.skip_failed`).
        # 켠 프로젝트의 실패를 건너뛰면 가리지 않은 원본이 분석으로 나간다.
        if (stage == "deident" and entry.status == "failed"
                and video.deident_status == "failed" and not deident_on):
            entry.skippable = True
        # 끝난 비식별화의 보고에 확인 필요(`review_needed`)가 있으면 경고를 싣는다.
        # 가릴 얼굴을 찾지 못한 결과는 원본과 같아서 화면에서 완료만 보이면 얼굴이 그대로 나간다.
        if stage == "deident" and entry.status == "ok":
            report = (video.extra or {}).get("deident")
            if isinstance(report, dict) and report.get("review_needed"):
                entry.review_needed = True
                entry.review_note = _review_note(report.get("review"))
        if entry.status == "running":
            # 인코딩 진행률은 워커 하트비트가 보낸 인코딩된 미디어 시간을 영상 길이로 나눈 값이다.
            # 두 값이 있을 때만 계산한다. ffmpeg가 길이를 조금 넘겨 보고할 수 있어 1에서 자른다.
            # transcode(분석본 마스터)와 publish(재생본)는 잡당 산출이 하나라서 분모가 영상 길이다.
            # 분모에 산출 수를 곱하면 진행률이 50%에서 멈춘다.
            # `h264 · 720p` 원본은 publish만 돌므로 publish에도 진행률을 계산한다.
            encoded = {
                "transcode": video.transcode_done_seconds,
                "publish": video.publish_done_seconds,
            }.get(stage)
            if encoded is not None and video.duration_seconds:
                entry.progress = min(
                    1.0, float(encoded) / float(video.duration_seconds))
            if (
                stage == "transcode" and video.transcode_status == "running"
                and video.transcode_lease_until and video.transcode_lease_until < now
            ):
                entry.note = "처리 서버 응답이 없습니다. 잠시 후 자동으로 다시 시도합니다."
        entries.append((entry, derived_row))

    # 실패했거나 할 일이 남은 단계 뒤의 대기 단계에는 대기 사유를 적는다.
    # 사유 없이 `대기`만 표시하면 곧 진행될 것처럼 읽힌다.
    after_failure = False
    for entry, derived_row in entries:
        if after_failure and entry.status == "pending" and derived_row and not entry.note:
            entry.note = "앞 단계가 끝나야 진행합니다."
        if entry.status in ("failed", "needed"):
            after_failure = True

    return PrepTimelineDTO(
        video_id=video_id, entries=[e for e, _ in entries], generated_at=now,
    )


#: 비식별화 검수 코드별 화면 문장. 코드 뒤의 `:` 이후는 세부 값이라 비교에서 뺀다.
_REVIEW_TEXT = {
    "no-detections": "얼굴을 찾지 못해 원본이 그대로 나갑니다.",
}


def _review_note(codes: object) -> str:
    """비식별화 검수 코드를 화면 문장으로 바꾼다. 모르는 코드는 감추지 않고 그대로 싣는다.

    코드가 없으면 확인이 필요하다는 문장만 쓴다.
    """
    said = [
        _REVIEW_TEXT.get(c.split(":")[0].strip(), c)
        for c in (codes if isinstance(codes, list) else []) if isinstance(c, str) and c
    ]
    return " ".join(said) if said else "비식별화 결과를 확인해야 합니다."


#: 분석 서비스가 처리하는 단계. 실패 사유를 분석 사유 규칙(`analysis_error_text`)으로 읽는다.
_ANALYSIS_STAGES = frozenset({"register", "analyze", "summarize"})


def _readable_run_error(stage: str, error: str | None) -> str | None:
    """기록된 시도의 실패 사유를 읽을 때 화면 문구로 바꾼다.

    저장할 때 변환(`stage_runs._screen_text`)을 거치지 않은 행에는 원문이 남아 있을 수 있어
    읽을 때도 한 번 더 변환한다. 두 변환 함수는 멱등이라 이미 화면 문구인 사유는 그대로 남는다.
    """
    if not error:
        return error
    if stage in _ANALYSIS_STAGES:
        return error_text.analysis_error_text(error)
    return error_text.operator_text(error)

