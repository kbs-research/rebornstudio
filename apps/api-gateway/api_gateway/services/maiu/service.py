"""MI 분석 요청과 잡 관리 서비스.

맡는 일은 다음과 같다.
- 분석 요청(dispatch)과 요청 전 게이트.
- 잡 목록·상세 조회(DTO는 MI 단계 `stage`를 그대로 내보낸다).
- 비용 추정.
- 실패한 잡 재시도.

요청 뒤의 진행은 `reconciler`의 5단계 상태 머신이 맡는다
(`pending` → `registering` → `registered` → `analyzing` → `analyzed` → `summarizing` → `done`).
"""
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import UUID

import anyio
from fastapi import HTTPException, status
from reborn_contracts import MaiuDispatchRequest, MaiuDispatchResult, MaiuJobDTO
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import AnalysisJob, Clip, DomainEvent, Project, User, Video, WorkItem
from api_gateway.services import audit, authz, error_text, operating_time, platform_credentials
from api_gateway.services.analysis import (
    job_state,
    presets,
    scene_options,
)
from api_gateway.services.analysis.registry import MAIU_KEY
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.naver_client import MaiuTransientError
from api_gateway.services.media import resolver, storage
from api_gateway.services.media import stage_runs as media_stage_runs

log = logging.getLogger(__name__)

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# MI에 요청한 work_item의 점유 만료 기간. 먼 미래로 두어 claim 큐의 만료 회수 대상에서 제외한다.
_MAIU_FAR_FUTURE = timedelta(days=365 * 10)


#: 일시 오류로 남긴 `error_msg`의 접두어. 남기는 곳(`reconciler.errors`), 지우는 곳
#: (`reconciler.stages`), 화면 표시를 정하는 곳(`_shown_error`)이 함께 쓴다.
TRANSIENT_MARK = "[일시]"


def _to_dto(j: AnalysisJob) -> MaiuJobDTO:
    """잡을 DTO로 바꾼다. MI 단계(`stage`)를 그대로 내보내 화면이 진행 단계를 표시하게 한다.

    DTO의 `status` 필드에는 `stage`를 넣는다.
    중립 `status`는 네 가지뿐이라 그 값으로는 화면이 어느 단계인지 표시할 수 없다.
    판정에는 `status`를, 표시에는 `stage`를 쓴다.

    `error`는 화면이 그대로 표시하는 한 줄로 내보낸다(`error_text.analysis_error_text`).
    저장된 `error_msg`에는 문장으로 바꾸기 전의 원문이나 `[일시]` 표지가 남은 행이 있다.
    그대로 내보내면 화면에 벤더 API 경로가 잘린 채 나오고 원인이 보이지 않는다.
    원문은 `payload["last_error_raw"]`와 감사 이벤트에 남아 있다.
    """
    return MaiuJobDTO(
        job_id=_synthetic_uuid(j.id),  # DTO가 UUID를 받으므로 BIGSERIAL을 UUID로 바꾼다
        project_id=j.project_id,
        video_id=j.video_id,
        # 영상 단위 잡이라 작업 식별자가 없다. 기존 API 소비자와 호환하려고 같은 값을 싣는다.
        work_item_id=j.video_id,
        maiu_asset_id=j.external_ref,
        maiu_analysis_id=(j.payload or {}).get("maiu_analysis_id"),
        status=j.stage or "pending",
        deferred=j.deferred,
        retry_count=j.attempt,
        in_flight_count=j.in_flight_count,
        error=_shown_error(j),
        trace_id=j.trace_id,
        dispatched_at=j.created_at,
        # 완료 여부는 중립 상태(`job_state.SETTLED`)로 판정한다.
        # 벤더 단계 목록을 여기 다시 적지 않는다.
        completed_at=j.updated_at if j.status in job_state.SETTLED else None,
    )


def _shown_error(j: AnalysisJob) -> str | None:
    """화면에 표시할 잡 오류를 정한다. 산출물이 나온 잡의 지난 일시 오류는 `None`으로 둔다.

    일시 오류 문구는 한 번의 요청이 실패했다는 기록이다.
    단계가 넘어가면 `reconciler.stages._advance_ok`가 지우지만 문구가 남은 채 끝난 행도 있다.
    그대로 표시하면 끝난 잡이 "요청량 제한을 초과했습니다" 문구를 단 채 목록에 보인다.
    """
    if j.status in job_state.PRODUCED and (j.error_msg or "").startswith(TRANSIENT_MARK):
        return None
    return error_text.analysis_error_text(j.error_msg)


def _synthetic_uuid(big_id: int) -> UUID:
    """BIGSERIAL id를 DTO용 UUID로 바꾼다. 외부에 노출하는 ID를 안정적으로 유지한다.

    `UUID(int=N, version=4)`는 N의 하위 62비트를 보존하고 version·variant 비트만 덮어쓴다.
    그래서 2^62 미만의 id는 서로 다른 UUID가 된다.
    """
    return UUID(int=big_id, version=4)


async def _source_missing(video: Video | None) -> bool:
    """원본 객체가 없다고 확인되면 True. 확인할 수 없으면 False라서 요청을 막지 않는다.

    `object_exists`가 None(권한·네트워크·설정 문제)을 돌려주면 통과시킨다.
    존재 확인이 불안정할 때 막으면 정상 요청까지 막혀 분석이 멈춘다.
    """
    uri = resolver.media_uri(video, "analysis_input") if video else None
    if not uri:
        return False
    try:
        bucket, key = _parse_source_uri(uri)
    except ValueError:
        return False
    exists = await anyio.to_thread.run_sync(
        partial(storage.object_exists, bucket=bucket, key=key)
    )
    return exists is False


def _parse_source_uri(uri: str) -> tuple[str, str]:
    """`s3://bucket/key`를 (bucket, key)로 나눈다. 형식이 다르면 ValueError를 던진다."""
    if not uri.startswith("s3://"):
        raise ValueError(uri)
    rest = uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(uri)
    return bucket, key


def _readable_dispatch_error(exc: Exception) -> str:
    """MI 예외를 화면에 그대로 표시할 한 줄로 바꾼다.

    문장 변환은 `error_text.operator_reason` 한 곳에서 한다.
    폴링 실패(reconciler)도 같은 함수를 써서 운영자가 두 화면에서 같은 설명을 본다.
    변환을 한쪽에만 두면 다른 쪽에는 내부 엔드포인트가 그대로 표시된다.

    돌려주는 값은 화면(`blockOf`)이 그대로 표시하는 완결된 사유다(`extra["dispatch_error"]`).
    앞 문구(`분석 요청을 보내지 못했습니다:`)는 `error_text.dispatch_error_text`가 붙인다.
    저장된 값 중 `분석 요청 거절 ·`로 시작하는 값도 읽을 때 같은 형식으로 바뀐다.
    """
    return error_text.dispatch_error_text(exc)


async def _mark_no_input(db: AsyncSession, video: Video, item: WorkItem) -> None:
    """분석 입력 경로가 없어 요청하지 않은 사실을 영상과 감사 기록에 남긴다."""
    from api_gateway.services.media.video import merge_extra
    await merge_extra(db, video.video_id, {"dispatch_error": error_text.NO_INPUT_TEXT})
    audit.emit(
        db, event_type="maiu.dispatch_source_missing", target_kind="video",
        target_id=video.video_id, project_id=video.project_id,
        payload={"source_uri": None, "work_item_id": str(item.work_item_id)},
    )


async def _mark_source_missing(db: AsyncSession, video: Video, item: WorkItem) -> None:
    """원본이 없어 요청하지 못한 사실을 영상과 감사 기록에 남긴다.

    화면(`blockOf`)은 `extra["dispatch_error"]` 하나만 읽어 '막힘' 사유로 표시한다.
    `videos`에는 분석 상태 컬럼이 없어서 실패는 이 필드에만 쓴다.
    """
    from api_gateway.services.media.video import merge_extra
    await merge_extra(db, video.video_id, {
        "dispatch_error": "원본 파일이 없습니다. 저장소에서 삭제됐거나 경로가 바뀌었습니다",
    })
    audit.emit(
        db, event_type="maiu.dispatch_source_missing", target_kind="video",
        target_id=video.video_id, project_id=video.project_id,
        payload={"source_uri": video.source_uri, "work_item_id": str(item.work_item_id)},
    )


async def dispatch(
    db: AsyncSession, project_id: UUID, payload: MaiuDispatchRequest, user: User
) -> MaiuDispatchResult:
    """available 상태인 영상 단위 work_item을 MI에 요청하고 요청 결과를 돌려준다.

    결과는 `MaiuDispatchResult`다(만든 잡 목록과 요청·보류·실패·건너뜀 수).
    MAIU는 영상을 분석해 클립을 만들므로 영상 단위(clip_id IS NULL)만 요청한다.
    클립 단위 work_item(라벨링 대기)은 제외한다.
    영상마다 잡은 하나다. 잡 생성은 영상마다 savepoint로 격리한다.
    이미 요청된 영상은 건너뜀(skipped) 수만 늘린다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            프로젝트 관리자가 아닌 경우(403 not_project_admin).
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)
    return await _dispatch_core(
        db, project_id, payload.video_ids, payload.limit, actor_user_id=user.user_id,
    )


async def _dispatch_core(
    db: AsyncSession,
    project_id: UUID,
    video_ids: list[UUID] | None,
    limit: int,
    *,
    actor_user_id: UUID | None,
    scene_opts: scene_options.SceneOptions | None = None,
) -> MaiuDispatchResult:
    """권한 검사 없이 dispatch를 실행한다.

    호출자는 권한 검사를 마친 `dispatch`(사용자 요청)와 보류 재요청(`redispatch_deferred`)이다.

    MI를 쓰지 않는 스택의 프로젝트는 요청하지 않고 예외 대신 빈 결과를 돌려준다.
    자체 분할 스택(`ncp-shots`)의 영상을 MI에 보내면 한 프로젝트에 두 벤더의 클립이 섞인다.
    라벨러는 그 차이를 알 수 없다. MI는 시간당 과금이라 쓰지 않을 분석에도 비용이 든다.
    프리셋을 프로젝트 생성 시 고정하는 이유도 같다.
    """
    project = await db.get(Project, project_id)
    # 이번 요청에 쓸 클립 길이다.
    # 일부 영상만 다른 길이로 분석할 수 있도록 요청 값이 프로젝트 값보다 우선한다.
    opts = scene_options.resolve(project.settings if project else None, scene_opts)
    stack = presets.of_project(project.settings if project else None)
    if stack.adapter_key != MAIU_KEY:
        log.info("MI 스택이 아니라 요청하지 않음 project=%s", project_id)
        return MaiuDispatchResult(created=[], requested=0)
    # 프로젝트가 고른 MI 연결·워크스페이스·MI 프로젝트다. 고르지 않았으면 `None`이다.
    target = platform_credentials.analysis_target_of(project.settings if project else None)
    await platform_credentials.ensure_loaded(db, target)
    # 저장소·분석 연동이 없으면 잡을 만들기 전에 409로 거절한다.
    # 잡을 만든 뒤에 실패하면 실패한 잡이 쌓인다.
    presets.require(stack, analysis=True, project_settings=project.settings if project else None)
    # 규격 게이트: ffprobe 규격 검사를 통과하고 길이를 아는 영상만 MI에 보낸다.
    # MI는 시간당 과금이고 깨진 파일·상한 초과·미지원 파일은 보내도 실패한다.
    # 검사 전(`pending`)도 막는다. 변환·재생본 단계는 검사를 통과해야 열리므로(`pipeline.open_next`)
    # 검사 전에 보내면 변환 전 원본(비 h264, 720p 초과)이 그대로 나간다.
    # 읽지 못한 파일(`error`)은 운영자가 재검사(`reset_probe`)로 다시 연다.
    from api_gateway.services.media import video as media_video

    # 준비 중(변환·비식별화·재생본)인 영상과 비식별화를 켜고 마스킹하지 않은 영상도 막는다.
    # `resolver.media_uri`는 마스킹본이 없으면 `analysis_uri or source_uri`를 돌려주므로
    # 이 게이트가 없으면 마스킹 전 원본이 MI로 나간다.
    # 자체 장면 분할 게이트와 화면 분류(`_ingest_bucket_case`의 'preparing')도 같은 함수
    # (`_prep_unfinished_expr`)로 판정한다. 한쪽만 고치면 다른 경로로 원본이 나간다.
    rejected_videos = select(Video.video_id).where(
        Video.project_id == project_id,
        media_video._prep_unfinished_expr(),
    )
    # 클립이 이미 있는 영상의 티켓은 고르지 않는다.
    # 클립을 만드는 경로가 티켓을 지우지만, 지우지 못한 기존 데이터가 남아 있을 수 있다.
    # 클립이 있는 영상을 보내면 MI 과금이 다시 일어나고 기존 클립과 겹치는 클립이 생긴다.
    # 티켓을 만드는 규칙(`work.service.ensure_for_video`)도 클립이 있는 영상을 뺀다.
    has_clips = select(Clip.clip_id).where(Clip.video_id == WorkItem.video_id).exists()
    q = select(WorkItem).where(
        WorkItem.project_id == project_id,
        WorkItem.status == "available",
        WorkItem.clip_id.is_(None),
        WorkItem.video_id.not_in(rejected_videos),
        ~has_clips,
    )
    if video_ids:
        q = q.where(WorkItem.video_id.in_(video_ids))
    # 선택 요청이면 선택한 수만큼, 지정하지 않으면 limit개를 한 번에 가져온다.
    cap = len(video_ids) if video_ids else limit
    items = (await db.scalars(
        q.order_by(WorkItem.created_at).limit(cap).with_for_update(skip_locked=True)
    )).all()
    # 고른 영상 중 이번에 가져오지 않은 영상은 게이트(규격·준비)에 걸렸거나 이미 요청된 영상이다.
    # 이 영상들의 보류 표시는 지운다. 남기면 화면에서 재요청을 기다리는 것처럼 보인다.
    picked = {item.video_id for item in items}
    missed = [v for v in dict.fromkeys(video_ids or []) if v not in picked]
    await _clear_deferred(db, project_id, missed)
    held_reasons = await _hold_reasons(db, project_id, missed)
    held = sum(held_reasons.values())
    if not items:
        await db.commit()
        return MaiuDispatchResult(
            created=[], requested=0, deferred=0, failed=0, skipped=0,
            held=held, held_reasons=held_reasons,
        )

    now = datetime.now(UTC)
    client = maiu_client.get_client(target)
    # 요청 당시 실제로 쓴 MI 선택을 잡마다 기록한다. 폴링·취소는 이 값으로 클라이언트를 고른다.
    pinned = maiu_client.pinned_target(target).as_dict()
    jobs: list[AnalysisJob] = []
    skipped = 0
    # items마다 SELECT하지 않도록 video를 한 번에 미리 읽는다.
    video_ids = {item.video_id for item in items}
    videos = {
        v.video_id: v
        for v in (await db.scalars(select(Video).where(Video.video_id.in_(video_ids))))
    }
    deferred = 0   # MI 요청량 제한 등 일시 오류로 이번에 보내지 못한 수. 실패와 따로 센다
    failed = 0     # 영상 자체의 문제로 보내지 못한 수. 나머지 영상은 계속 요청한다
    deferred_items: list[WorkItem] = []
    deferred_reason: str | None = None
    deferred_kind: str | None = None
    # 확장자를 붙이며 새 키로 복사한 원본의 옛 키. 커밋한 뒤에 지운다.
    renamed: list[tuple[str, str]] = []
    for idx, item in enumerate(items):
        video = videos.get(item.video_id)
        # 지난 요청이 남긴 막힘 사유가 있는지 먼저 읽어 둔다.
        # 아래 `merge_extra`가 `extra`를 만료시킨다.
        # 그 뒤에 읽으면 비동기 세션에서 암묵적 IO가 일어난다.
        had_error = video is not None and "dispatch_error" in (video.extra or {})
        # 분석 입력은 `resolver.media_uri` 한 곳이 고른다.
        # 분석용 사본(`analysis_uri`)과 비식별화 산출물의 우선순위도 그 함수가 정한다.
        input_uri = resolver.media_uri(video, "analysis_input") if video else None
        # 분석 입력 경로가 없으면 요청하지 않는다. 그대로 보내면 클라이언트의 경로 파싱 오류가
        # 사유로 저장되어 운영자가 원인을 알 수 없다.
        # 경로가 있으면 원본 객체가 있는지 확인한다. MI는 URI만 받아 비동기로 가져가므로
        # 없는 객체는 에셋이 생긴 뒤 수 분이 지나 import 실패로 드러난다.
        # 등록 뒤 지워진 경우도 같다.
        # 확인할 수 없으면(권한·네트워크 문제로 `None`) 통과시킨다. probe 게이트와 같은 원칙이다.
        if video is not None and not input_uri:
            log.warning("dispatch 건너뜀 — 분석 입력 경로 없음: video=%s", item.video_id)
            await _mark_no_input(db, video, item)
            failed += 1
            continue
        missing = await _source_missing(video)
        if missing:
            log.warning(
                "dispatch 건너뜀 — 분석 입력 객체 없음: video=%s uri=%s",
                item.video_id, input_uri,
            )
            await _mark_source_missing(db, video, item)
            failed += 1
            continue
        # MI는 키 확장자로 형식을 판별한다. 확장자 없는 업로드 원본(`intake/{id}/source`)은
        # 형식 오류로 거절된다. 그래서 원본을 확장자가 붙은 새 키로 복사해 보낸다.
        # 옛 키는 커밋한 뒤에 지운다(`renamed`).
        if video is not None:
            try:
                input_uri, old = await media_video.ensure_input_ext(
                    db, video, maiu_client.INPUT_EXTS)
                if old:
                    renamed.append(old)
            except Exception as exc:
                log.exception("분석 입력 복사 실패 video=%s", item.video_id)
                await media_video.merge_extra(db, video.video_id, {
                    "dispatch_error": _readable_dispatch_error(exc),
                })
                failed += 1
                continue
        req = {
            "work_item_id": str(item.work_item_id),
            "video_uri": input_uri,
            "video_title": video.title if video else "",
        }
        try:
            asset_id = await client.dispatch(request=req)  # 두 클라이언트 모두 asset_id를 바로 준다
        except MaiuTransientError as exc:
            # 429·5xx·타임아웃이다. 남은 영상은 보류하고 여기까지 성공한 요청은 커밋한다.
            # 예외를 밖으로 내보내면 500이 되고, 커밋 전이라 앞서 만든 잡 행이 롤백된다.
            # 그 영상들은 이미 MI에 등록돼 있어 서버 DB에만 기록이 없는 고아 에셋이 된다.
            # 사용자가 다시 요청하면 같은 영상이 또 등록되어 과금이 겹친다.
            log.warning("maiu dispatch deferred (transient): %s", exc)
            deferred_items = list(items[idx:])
            deferred_reason = error_text.operator_reason(exc)
            deferred_kind = error_text.analysis_transient_kind(exc.status)
            deferred = len(deferred_items)
            break
        except Exception as exc:
            # 이 영상 하나의 문제(잘못된 source_uri, MI 설정 등)라 나머지 요청은 계속한다.
            # 사유는 영상의 `dispatch_error`에 남기고 화면(`blockOf`)이 '막힘' 사유로 표시한다.
            # 로그에만 남기면 화면에는 계속 '분석 요청 대기'로 보인다.
            # 원본 없음(`_mark_source_missing`)도 같은 필드를 쓴다.
            log.exception("maiu dispatch failed for video=%s", item.video_id)
            if video is not None:
                from api_gateway.services.media.video import merge_extra
                await merge_extra(db, video.video_id, {
                    "dispatch_error": _readable_dispatch_error(exc),
                })
            failed += 1
            continue
        # 영상마다 잡은 하나다(idx_maiu_video UNIQUE). savepoint로 격리한다.
        async with db.begin_nested():
            try:
                job = AnalysisJob(
                    video_id=item.video_id,
                    project_id=project_id,
                    # MI는 영상 단위로만 분석한다. 클립 단위는 다른 어댑터가 맡는다.
                    clip_id=None,
                    adapter_key=MAIU_KEY,
                    external_ref=asset_id,
                    # 등록 요청 하나가 나가 있다. 등록 완료 폴링이 이 수를 내린다.
                    in_flight_count=1,
                    # 요청 시점의 클립 길이를 함께 기록한다.
                    # 대기 중에 프로젝트 설정이 바뀌어도 이 잡은 운영자가 고른 값으로 나간다.
                    # 나중에 클립을 어떤 길이 설정으로 잘랐는지도 확인할 수 있다.
                    payload={**req, "scene_options": opts.model_dump(),
                             maiu_client.JOB_TARGET_KEY: pinned},
                )
                # 위 `client.dispatch`가 이미 에셋을 등록했다. 그래서 잡은 등록 완료를 기다리는
                # 단계(`registering`)에서 시작하고, reconciler는 이 에셋의 import 상태만 조회한다.
                # 대기 단계로 넣으면 reconciler가 같은 영상을 다시 등록해 에셋이 둘이 된다.
                # 그러면 첫 에셋은 쓰이지 않고 과금만 남는다.
                job_state.advance(job, "registering")
                db.add(job)
                await db.flush()
            except IntegrityError:
                skipped += 1
                continue
        # 요청 게이트 결과를 MI 등록 단계의 첫 세부 항목으로 남긴다.
        # 게이트는 규격·변환 상태(위 `rejected_videos`)와 원본 객체(`_source_missing`)를 확인한다.
        # 막힌 경우만 `dispatch_error`로 남기면 통과한 영상은 화면에 게이트 결과가 표시되지 않는다.
        # 게이트는 등록 직전에 하는 일이라 새 단계를 만들지 않고 등록 단계의 세부 항목으로 둔다.
        await media_stage_runs.start(db, item.video_id, "register", detail={
            "gate_probe": video.probe_status if video else None,
            "gate_transcode": video.transcode_status if video else None,
            "gate_deident": video.deident_status if video else None,
            "gate_publish": video.publish_status if video else None,
            "gate_source": "확인됨",   # `_source_missing`이 위에서 이미 걸렀다
        })
        # 지난 요청이 남긴 막힘 사유를 지운다.
        # 남기면 다시 요청해 나간 영상에도 거절 사유가 표시된다.
        if had_error:
            await media_video.merge_extra(db, item.video_id, {}, remove=("dispatch_error",))
        item.status = "claimed"
        item.claimed_by = None   # 점유자 없는 `claimed`가 MI 요청 표시다
        item.claimed_at = now
        item.expires_at = now + _MAIU_FAR_FUTURE
        jobs.append(job)
    # 보류 표시를 정리한다. 이번에 처리가 끝난 영상(요청·실패·건너뜀)은 표시를 지우고
    # 보류된 영상에는 새로 남긴다. 화면은 이 표시로 "보류됨"을 표시한다.
    # reconciler는 이 표시로 재요청 대상을 고른다(`redispatch_deferred`).
    # 운영자가 고른 클립 길이도 함께 남긴다. 재요청이 프로젝트 기본값으로 나가면
    # 운영자가 고른 값과 실제로 나간 값이 달라진다.
    deferred_ids = {i.video_id for i in deferred_items}
    await _clear_deferred(
        db, project_id, [i.video_id for i in items if i.video_id not in deferred_ids],
    )
    if deferred_ids:
        from api_gateway.services.media.video import merge_extra
        # `kind`는 화면에 표시할 짧은 사유 이름이다.
        # `retry_by`는 다음 재요청이 늦어도 시작되는 시각이다.
        # 보류된 영상이 한 번에 다시 보내는 수(`_REDISPATCH_BATCH`)보다 많으면 그보다 늦을 수 있다.
        mark = {
            "at": now.isoformat(),
            "reason": deferred_reason,
            "kind": deferred_kind,
            "retry_by": (now + timedelta(
                seconds=_REDISPATCH_AFTER_S + _DEFERRED_CYCLE_S)).isoformat(),
            "scene_options": scene_opts.model_dump() if scene_opts else None,
        }
        for vid in deferred_ids:
            await merge_extra(db, vid, {DEFERRED_KEY: mark})
    # 아래 프로젝트 요약 이벤트(요청 건수)와 별도로 영상마다 이벤트를 남긴다.
    # 요약만 남기면 영상 단위 감사 기록(`target_id=video_id`)에 요청 시각이 남지 않는다.
    # 요청한 클립 길이도 함께 남긴다. 잡 payload에도 있지만 감사 로그만으로 클립 길이의 근거를
    # 확인할 수 있게 한다.
    for j in jobs:
        audit.emit(
            db, event_type="maiu.requested", target_kind="video", target_id=j.video_id,
            actor_user_id=actor_user_id, project_id=project_id,
            payload={"asset_id": j.external_ref, "scene_options": opts.model_dump()},
        )
    audit.emit(
        db, event_type="maiu.dispatched", target_kind="project", target_id=project_id,
        actor_user_id=actor_user_id, project_id=project_id,
        payload={"count": len(jobs), "skipped_count": skipped,
                 "deferred": deferred, "failed": failed, "requested": len(items)},
    )
    await db.commit()
    await _drop_renamed(renamed)
    for j in jobs:
        await db.refresh(j)
    return MaiuDispatchResult(
        created=[_to_dto(j) for j in jobs],
        requested=len(items), deferred=deferred, failed=failed, skipped=skipped,
        held=held, held_reasons=held_reasons,
    )


async def _drop_renamed(renamed: list[tuple[str, str]]) -> None:
    """확장자를 붙여 새 키로 옮긴 원본의 옛 키를 지운다. 커밋한 뒤에만 부른다.

    지우지 못해도 요청은 이미 커밋됐으므로 실패로 돌리지 않는다. 남은 옛 키는 용량만 차지한다.
    """
    for bucket, key in renamed:
        try:
            await anyio.to_thread.run_sync(
                partial(storage.delete_object, bucket=bucket, key=key))
        except Exception:  # noqa: BLE001 — 한 건 실패가 응답을 막지 않는다
            log.exception("옛 원본 키 삭제 실패 bucket=%s key=%s", bucket, key)


async def _hold_reasons(
    db: AsyncSession, project_id: UUID, video_ids: list[UUID]
) -> dict[str, int]:
    """고른 영상 중 이번에 가져오지 않은 영상을 사유별로 세어 돌려준다.

    사유는 요청 게이트와 같은 판정(`media.video._analysis_hold_case`)으로 정한다.
    준비 판정에 걸리지 않은 영상의 사유는 요청 대기 항목이 없다는 뜻의 `not_waiting`이다.
    `not_waiting`에는 이미 요청한 영상, 다른 프로젝트의 영상, 삭제된 영상이 든다.
    """
    if not video_ids:
        return {}
    from api_gateway.services.media import video as media_video

    rows = dict((await db.execute(
        select(
            Video.video_id,
            func.coalesce(media_video._analysis_hold_case(), "not_waiting"),
        ).where(
            Video.project_id == project_id,
            Video.deleted_at.is_(None),
            Video.video_id.in_(video_ids),
        )
    )).all())
    counts: dict[str, int] = {}
    for vid in video_ids:
        reason = rows.get(vid, "not_waiting")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


#: 일시 오류로 보내지 못한 영상의 보류 표시 키(`videos.metadata`).
#: 영상 DTO도 읽으므로 도메인 공용 모듈(`error_text`)에 둔다.
DEFERRED_KEY = error_text.DISPATCH_DEFERRED_KEY

#: 보류된 영상을 다시 요청하기 전에 기다리는 시간(초). 스로틀이 풀릴 시간을 둔다.
_REDISPATCH_AFTER_S = 30
#: reconciler 보류 사이클의 간격(초).
#: beat 5초 주기에 정상 10번·보류 1번을 섞는다(`reconciler.jobs`).
#: 보류된 영상은 `_REDISPATCH_AFTER_S`가 지난 뒤 첫 보류 사이클에 다시 나간다.
_DEFERRED_CYCLE_S = 55
#: 한 번에 다시 요청하는 영상 수의 상한. 막혔던 요청이 한꺼번에 몰리면 다시 스로틀에 걸린다.
_REDISPATCH_BATCH = 20


async def _clear_deferred(db: AsyncSession, project_id: UUID, video_ids: list[UUID]) -> None:
    """이 프로젝트 영상 중 보류 표시가 있는 영상에서만 표시를 지운다.

    표시가 없는 영상에는 쓰기를 하지 않는다.
    `video_ids`에는 요청자가 고른 id가 그대로 들어온다. 프로젝트 조건이 없으면 한 프로젝트의
    요청 권한으로 다른 프로젝트 영상의 표시를 지워 보류 사이클의 재요청에서 빼게 된다.
    """
    if not video_ids:
        return
    from api_gateway.services.media.video import merge_extra
    marked = await db.scalars(
        select(Video.video_id).where(
            Video.project_id == project_id,
            Video.video_id.in_(video_ids), Video.extra.has_key(DEFERRED_KEY),
        )
    )
    for vid in marked.all():
        await merge_extra(db, vid, {}, remove=(DEFERRED_KEY,))


async def redispatch_deferred(
    db: AsyncSession, *, limit: int = _REDISPATCH_BATCH,
    min_age_seconds: float = _REDISPATCH_AFTER_S,
) -> int:
    """일시 오류로 보류된 분석 요청을 다시 보낸다. reconciler의 보류 사이클이 부른다.

    보류는 MI의 429·5xx·타임아웃에서 생기고, 이 오류는 시간이 지나면 풀린다.
    사람이 다시 누를 때까지 기다리면 "요청 대기"로 보이는 영상이 실제로는 멈춰 있다.
    오래된 보류부터 `limit`건을 프로젝트·클립 길이별로 묶어 `_dispatch_core`로 보낸다.
    게이트·원본 확인·잡 생성 규칙은 사람이 누른 요청과 같다. 다시 보류되면 표시 시각이
    새로 찍혀 `min_age_seconds` 뒤에 다시 시도한다.
    게이트에 걸린 영상(그 사이 제외·규격 실패)은 요청하지 않고 표시만 지운다.

    Returns:
        int: 이번에 만든 분석 잡 수.
    """
    rows = (await db.execute(
        select(Video.video_id, Video.project_id, Video.extra[DEFERRED_KEY])
        .where(Video.extra.has_key(DEFERRED_KEY), Video.deleted_at.is_(None))
        .order_by(Video.extra[DEFERRED_KEY]["at"].astext)
        .limit(limit)
    )).all()
    cutoff = datetime.now(UTC) - timedelta(seconds=min_age_seconds)
    groups: dict[tuple[UUID, str], list[UUID]] = {}
    for vid, pid, mark in rows:
        mark = mark if isinstance(mark, dict) else {}
        try:
            at = datetime.fromisoformat(str(mark.get("at")))
        except ValueError:
            at = None
        if at is not None and at > cutoff:
            continue
        key = (pid, json.dumps(mark.get("scene_options"), sort_keys=True))
        groups.setdefault(key, []).append(vid)
    created = 0
    for (pid, opts_json), vids in groups.items():
        raw = json.loads(opts_json)
        try:
            opts = scene_options.SceneOptions(**raw) if isinstance(raw, dict) else None
        except ValueError:
            opts = None
        result = await _dispatch_core(
            db, pid, vids, len(vids), actor_user_id=None, scene_opts=opts,
        )
        created += len(result.created)
    return created


async def list_for_project(
    db: AsyncSession, project_id: UUID, user: User
) -> MaiuDispatchResult:
    """프로젝트의 MAIU 잡 목록을 최신순으로 돌려준다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            프로젝트 관리자가 아닌 경우(403 not_project_admin).
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)
    rows = await db.scalars(
        select(AnalysisJob)
        .where(AnalysisJob.project_id == project_id)
        .order_by(AnalysisJob.created_at.desc())
    )
    return [_to_dto(j) for j in rows]


async def cost_stats(
    db: AsyncSession, project_id: UUID, user: User, *, month: str | None = None
) -> dict:
    """한 프로젝트의 MI 추정 비용을 돌려준다(project_admin). 산식은 `_cost_core`다."""
    await authz.require_project_admin(db, project_id, user)
    return await _cost_core(db, project_id=project_id, month=month)


async def _cost_core(
    db: AsyncSession, *, project_id: UUID, month: str | None
) -> dict:
    """한 프로젝트의 MI 추정 비용을 계산한다.

    1시간 영상을 장면 요약까지 처리하는 비용을 `maiu_cost_per_hour_krw`(기본 1만원)로 추정한다.
    estimated_cost_krw = (처리 완료(done) 영상 길이 합 / 3600) × 시간당 단가.
    비용의 근거를 볼 수 있도록 단계별 호출 건수(by_stage)도 함께 돌려준다.

    남은 비용(`remaining_*`)은 아직 done이 아닌 영상으로 계산한다.
    남은 분량은 기간별 지출이 아니라 현재 잔량이라서 month를 적용하지 않는다.

    Raises:
        HTTPException: month가 `YYYY-MM` 형식이 아닌 경우(422 invalid_month).
    """
    if month is not None and not _MONTH_RE.match(month):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_month")

    # stage별 호출 건수. `maiu.stage.*` 이벤트를 센다.
    ev_stmt = (
        select(DomainEvent.event_type, func.count().label("cnt"))
        .where(
            DomainEvent.event_type.like("maiu.stage.%"),
            DomainEvent.project_id == project_id,
        )
        .group_by(DomainEvent.event_type)
    )
    if month is not None:
        ev_stmt = ev_stmt.where(
            operating_time.month_key_expr(DomainEvent.occurred_at) == month
        )
    by_stage = {
        et.replace("maiu.stage.", ""): cnt for et, cnt in (await db.execute(ev_stmt)).all()
    }

    # 처리 완료(done) 영상의 길이 합으로 시간 기반 비용을 추정한다.
    # 영상당 잡은 idx_maiu_video UNIQUE로 1행이 보장되지만 distinct 영상 기준으로 센다.
    # 픽스처나 재시도 로직이 done 행을 중복으로 만들어도 비용이 배로 늘지 않게 한다.
    done_videos = (
        select(Video.video_id.label("vid"), Video.duration_seconds.label("dur"))
        .join(AnalysisJob, AnalysisJob.video_id == Video.video_id)
        .where(
            AnalysisJob.project_id == project_id,
            AnalysisJob.status == "done",
            AnalysisJob.adapter_key == MAIU_KEY,
        )
    )
    if month is not None:
        done_videos = done_videos.where(
            operating_time.month_key_expr(AnalysisJob.updated_at) == month
        )
    sub = done_videos.distinct().subquery()
    processed_count, total_seconds = (await db.execute(
        select(func.count(), func.coalesce(func.sum(sub.c.dur), 0.0)).select_from(sub)
    )).one()
    total_hours = float(total_seconds or 0.0) / 3600.0
    rate = settings.maiu_cost_per_hour_krw

    # 남은 비용은 아직 분석하지 않은 분량으로 계산한다.
    # 누적 비용만으로는 예산이 남는지 판단할 수 없다.
    # 모집단은 dispatch가 실제로 보낼 영상과 같다. 삭제된 영상은 뺀다.
    # probe·변환 상태는 보내는 시점만 바꾸고 보낼지는 바꾸지 않으므로 거르지 않는다.
    # 남은 분량은 현재 잔량이라 month를 적용하지 않는다. 월 필터는 위의 지출과 호출 건수에만 쓴다.
    done_ids = select(AnalysisJob.video_id).where(
        AnalysisJob.project_id == project_id,
        AnalysisJob.status == "done",
        # MI 잡만 센다. 스택이 둘이 되면 다른 스택의 작업이 MI 비용 집계에 섞인다.
        AnalysisJob.adapter_key == MAIU_KEY,
    )
    remaining_seconds, remaining_unmeasured = (await db.execute(
        select(
            func.coalesce(func.sum(Video.duration_seconds), 0.0),
            # 길이를 모르는 영상은 합계에서 빠지므로 그 수를 따로 센다.
            # 화면은 이 수로 비용 계산에서 빠진 영상 수를 표시한다.
            func.count().filter(Video.duration_seconds.is_(None)),
        ).where(
            Video.project_id == project_id,
            Video.deleted_at.is_(None),
            Video.video_id.not_in(done_ids),
        )
    )).one()
    remaining_hours = float(remaining_seconds or 0.0) / 3600.0

    return {
        "processed_video_count": processed_count or 0,
        "total_video_hours": round(total_hours, 2),
        "estimated_cost_krw": round(total_hours * rate),
        "remaining_video_hours": round(remaining_hours, 2),
        "remaining_cost_krw": round(remaining_hours * rate),
        "remaining_unmeasured_count": remaining_unmeasured or 0,
        "cost_per_hour_krw": rate,
        "total_calls": sum(by_stage.values()),
        "by_stage": by_stage,
    }


async def retry_failed(
    db: AsyncSession, project_id: UUID, video_ids: list[UUID], user_id: UUID | None,
) -> dict:
    """실패한 MI 잡을 다시 시작한다. 실패한 단계에 따라 되돌릴 지점이 다르다.

    장면 요약을 받지 못한 잡(`media.video._summary_blocked_expr`)은 요약만 다시 요청한다.
    같은 분석 번호로 `request_scene_summary`를 한 번 부르고 잡을 `summarizing`으로 되돌린다.
    분석과 클립은 그대로 두고 이미 작업에 풀린 work_item도 건드리지 않는다.
    요약을 받으면 reconciler가 클립에 요약을 붙이고 잡을 `done`으로 닫는다.
    요약 요청이 실패하면 잡을 `done`으로 두고 단계 이력에 새 실패를 남긴다. 영상은 막힘으로 남는다.

    에셋(`external_ref`)이 있는 실패 잡은 분석·요약 단계에서 실패한 잡으로 본다.
    잡을 `registered`로 되돌리면 reconciler가 같은 에셋으로 분석을 다시 요청한다.
    재등록하지 않는다. 같은 영상이 MI에 두 번 올라가면 어느 에셋이 맞는지 가릴 수 없고
    업로드와 전송도 다시 일어난다.

    에셋이 없으면 등록 단계에서 실패한 잡이다. 잡을 지우고 디스패치 티켓을 다시 만들어
    처음부터 진행한다. 티켓 생성은 `ensure_for_video`에 맡겨 정상 경로와 같은 규칙을 쓴다.

    원본이 잠깐 사라졌다 돌아온 경우처럼 원인이 일시적이어도,
    실패한 영상은 이 함수로만 다시 진행된다.
    분석은 시간당 과금이라 자동 재시도하지 않는다.
    호출부(사람이 누르는 버튼)가 비용을 알린 뒤 부른다.

    잡 행은 `FOR UPDATE SKIP LOCKED`로 잠근다. reconciler가 처리 중인 행이나 같은 요청이 먼저
    잡은 행은 건너뛰고, 먼저 잡은 쪽이 커밋하면 단계가 바뀌어 다시 고르지 않는다.

    Returns:
        dict: `requeued`(분석 재요청 수), `restarted`(등록부터 다시 시작한 수),
        `resummarized`(요약 재요청 수), `resummarize_failed`(요약 요청이 실패한 수),
        `skipped`(재요청할 상태가 아니라서 제외한 수).
    """
    from api_gateway.services.maiu.reconciler.stages import _NEXT_POLL_KEY, _analysis_id
    from api_gateway.services.media.video import _summary_blocked_expr, merge_extra
    from api_gateway.services.work import service as work_service

    project = await db.get(Project, project_id)
    await platform_credentials.ensure_loaded(
        db, platform_credentials.analysis_target_of(project.settings if project else None))
    presets.require(presets.of_project(project.settings if project else None), analysis=True,
                    project_settings=project.settings if project else None)
    jobs = (await db.scalars(
        select(AnalysisJob)
        .join(Video, Video.video_id == AnalysisJob.video_id)
        .where(
            AnalysisJob.project_id == project_id,
            AnalysisJob.video_id.in_(video_ids),
            AnalysisJob.adapter_key == MAIU_KEY,
            # 요약을 받지 못한 잡은 `done`이라 실패 단계 조건만으로는 고를 수 없다.
            # 막힘 판정과 같은 조건(`_summary_blocked_expr`)으로 고른다.
            (AnalysisJob.stage == "failed")
            | ((AnalysisJob.stage == "done") & _summary_blocked_expr()),
        )
        .with_for_update(of=AnalysisJob, skip_locked=True)
    )).all()

    requeued = restarted = resummarized = resummarize_failed = 0
    for job in jobs:
        if job.stage == "done":
            ok = await _resummarize(db, job, user_id=user_id, analysis_id=_analysis_id(job),
                                    poll_key=_NEXT_POLL_KEY)
            if ok:
                resummarized += 1
            else:
                resummarize_failed += 1
            continue
        if job.external_ref:
            # 분석부터 다시 한다. 에셋은 그대로 쓴다.
            job_state.advance(job, "registered")
            job.error_msg = None
            requeued += 1
        else:
            video = await db.get(Video, job.video_id)
            await db.delete(job)
            # 사용한 티켓을 지우고 다시 만든다. `ensure_for_video`는 멱등이라
            # `claimed`로 남은 행이 있으면 새로 만들지 않는다. 그러면 영상이 다시 진행되지 않는다.
            # 티켓은 분석이 필요하다는 표시이고 조건 판정은 `ensure_for_video` 한 곳이 맡는다.
            await db.execute(
                delete(WorkItem).where(
                    WorkItem.video_id == job.video_id, WorkItem.clip_id.is_(None)
                )
            )
            if video is not None:
                await work_service.ensure_for_video(db, video)
            restarted += 1
        # 지난 실패 표시는 지운다. 남겨 두면 화면이 계속 '막힘'으로 표시한다.
        await merge_extra(db, job.video_id, {}, remove=("dispatch_error",))
        # 영상 단위로 이벤트를 남긴다. 프로젝트 대상 요약만 남기면
        # 영상 단위 감사 기록(`target_id=video_id`)에 이 영상의 재시도 기록이 남지 않는다.
        audit.emit(
            db, event_type="maiu.retried", target_kind="video", target_id=job.video_id,
            actor_user_id=user_id, project_id=project_id,
            payload={"mode": "requeue" if job.external_ref else "restart",
                     "asset_id": job.external_ref},
        )

    audit.emit(
        db, event_type="maiu.retry_failed", target_kind="project", target_id=project_id,
        actor_user_id=user_id, project_id=project_id,
        payload={"requeued": requeued, "restarted": restarted,
                 "resummarized": resummarized, "resummarize_failed": resummarize_failed,
                 "requested": len(video_ids)},
    )
    await db.commit()
    handled = requeued + restarted + resummarized + resummarize_failed
    return {"requeued": requeued, "restarted": restarted,
            "resummarized": resummarized, "resummarize_failed": resummarize_failed,
            "skipped": len(video_ids) - handled}


async def _resummarize(
    db: AsyncSession, job: AnalysisJob, *, user_id: UUID | None, analysis_id: str | None,
    poll_key: str,
) -> bool:
    """요약을 받지 못한 잡의 장면 요약만 다시 요청한다. 요청이 받아들여지면 True다.

    `retry_failed`가 잠근 잡에 부른다. 분석을 다시 요청하지 않으므로 분석 비용이 다시 들지 않는다.
    단계 이력에 `summarize` 시도를 새로 열어 판정이 막힘에서 요약 중으로 바뀌게 한다.
    요청이 실패하면 연 시도를 실패로 닫아 영상은 막힘으로 남고 준비 타임라인에 새 사유가 보인다.
    감사 이벤트 `maiu.stage.summarize_requested`는 reconciler의 요약 요청과 같은 이름이라
    비용 통계의 단계별 호출 수(`by_stage`)에 한 건으로 더해진다.
    """
    from api_gateway.services.maiu.reconciler.common import _job_target

    await media_stage_runs.start(db, job.video_id, "summarize", detail={"resummarize": True})
    try:
        if not analysis_id:
            raise RuntimeError("no analysis_id")
        client = await maiu_client.client_for_job(db, job)
        sid = await client.request_scene_summary(analysis_id)
    except Exception as e:  # noqa: BLE001 — 한 건의 요청 실패가 나머지 재요청을 막지 않는다
        await media_stage_runs.finish(
            db, job.video_id, "summarize", ok=False,
            error=error_text.operator_reason(e)[:400])
        audit.emit(
            db, event_type="maiu.stage.summarize_request_failed", target_kind="maiu_job",
            target_id=_job_target(job), project_id=job.project_id, actor_user_id=user_id,
            payload={"resummarize": True, "error": error_text.raw_detail(e, 200)},
        )
        return False
    job.payload = {
        **{k: v for k, v in (job.payload or {}).items()
           if k not in (poll_key, "retry_after", "transient_streak", "transient_stage")},
        "scene_summary_id": sid,
    }
    job_state.advance(job, "summarizing")
    job.error_msg = None
    job.in_flight_count += 1
    audit.emit(
        db, event_type="maiu.stage.summarize_requested", target_kind="maiu_job",
        target_id=_job_target(job), project_id=job.project_id, actor_user_id=user_id,
        payload={"scene_summary_id": sid, "resummarize": True},
    )
    # 분석을 다시 요청한 기록(`maiu.retried`)과 종류를 나눈다. 같은 종류로 남기면 활동 이력에서
    # 요약만 다시 요청한 일이 분석 재시도로 읽힌다.
    audit.emit(
        db, event_type="maiu.summary_retried", target_kind="video", target_id=job.video_id,
        actor_user_id=user_id, project_id=job.project_id,
        payload={"scene_summary_id": sid, "analysis_id": analysis_id},
    )
    return True
