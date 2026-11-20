"""영상 포스터 썸네일 생성.

클립 썸네일(`clip_thumbnail`)과 같은 방법으로 영상 앞부분의 프레임 1장을 추출한다.
키는 `video.metadata.poster_thumbnail_key`에, 버킷은 `poster_thumbnail_bucket`에 기록한다.
비식별화를 쓰는 영상은 비식별화본이 확정된 뒤 비식별화본에서 추출한다(`thumbnail_source`).
클립은 MAIU 분석 뒤에 생기므로 그전까지 포스터를 대표 이미지로 표시한다.
대표 썸네일은 첫 클립 썸네일이고, 클립 썸네일이 없으면 포스터를 쓴다.

실패 정책은 `clip_thumbnail`과 같다. 영구 실패는 `poster_retry_count`를 올리고,
`_MAX_RETRIES`에 닿으면 `poster_failed=true`로 확정한다(dead-letter).
일시 오류는 재시도 횟수에 세지 않고 지수 백오프 뒤에 다시 시도한다.

beat 태스크 `run_pending_video_thumbnails`(worker-io, 1분 주기)가 부른다.
처리량은 한 주기에 `min(BATCH_LIMIT, TIME_BUDGET_S / t)`건이다.
같은 큐의 클립 썸네일·규격 검사와 워커를 나눠 쓰도록 한 번에 하나만 실행하고,
클립보다 짧은 예산 안에서 멈춘다.
"""
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from api_gateway.models import User, Video
from api_gateway.services.joblock import single_runner
from api_gateway.services.media import storage, thumbnail_source
from api_gateway.services.media.clip import thumbnail_reason
from api_gateway.services.transient import is_transient_boto

log = logging.getLogger(__name__)

# 포스터를 올리는 버킷 프리픽스와 `extra` 키
_POSTER_PREFIX = "video-posters"
_KEY = "poster_thumbnail_key"
#: 포스터를 올린 버킷의 `extra` 키.
BUCKET_KEY = "poster_thumbnail_bucket"
#: 포스터를 추출한 파일의 종류(`thumbnail_source.MASKED`·`SOURCE`)를 담는 `extra` 키.
#: 이 키가 없는 행은 원본에서 추출한 포스터로 본다.
_FROM_KEY = "poster_thumbnail_from"
_ERROR_KEY = "poster_error"
_RETRY_KEY = "poster_retry_count"
_FAILED_KEY = "poster_failed"
_MAX_RETRIES = 3

# 일시 오류의 연속 횟수와 다음 시도 시각의 키. 백오프 방식은 `clip_thumbnail`과 같다.
_STREAK_KEY = "poster_transient_streak"
_RETRY_AFTER_KEY = "poster_retry_after"
_BASE_BACKOFF_S = 60
_MAX_BACKOFF_S = 3600
_POSTER_TS = 3.0  # 추출할 프레임 위치(초). 검은 인트로·무신호 프레임을 피한다
#: 한 주기에 가져오는 영상 수의 상한.
BATCH_LIMIT = 20
#: 한 주기의 처리 시간 예산(초). 클립 썸네일 예산(`clip_thumbnail.TIME_BUDGET_S`)과 합쳐도
#: beat 주기(60초) 안에서 q.media 워커(동시 2) 한 슬롯의 몫을 넘지 않는다.
TIME_BUDGET_S = 15.0
#: 동시 실행을 막는 advisory lock 키('RBTV'). 락 규약은 `services/joblock.py`에 있다.
LOCK_KEY = 0x52425456


def pending_select() -> Select[tuple[Video]]:
    """포스터 후보의 조건만 담은 select를 만든다. 상한과 정렬은 `list_pending`이 더한다.

    조건과 상한을 나누는 이유는 `clip_thumbnail.pending_select`와 같다.
    """
    return select(Video).where(
        ~func.jsonb_exists(Video.extra, _KEY),
        Video.extra[_FAILED_KEY].astext.is_distinct_from("true"),
        # 일시 오류로 백오프 중인 영상은 뺀다. 포함하면 막힌 영상이 매 주기 상한을 차지한다.
        or_(
            Video.extra[_RETRY_AFTER_KEY].astext.is_(None),
            Video.extra[_RETRY_AFTER_KEY].astext <= datetime.now(UTC).isoformat(),
        ),
        # 비식별화본에서 추출하는 영상은 원본(`source_uri`)이 없어도 후보다.
        or_(Video.source_uri.isnot(None), Video.deident_status == "done"),
        Video.deleted_at.is_(None),
        # 비식별화를 쓰는 영상은 비식별화본이 확정될 때까지 기다린다(`thumbnail_source`).
        thumbnail_source.ready_expr(),
    )


async def invalidate_unmasked(db: AsyncSession) -> int:
    """비식별화를 쓰는 영상 중 원본에서 추출한 포스터의 키를 지우고 지운 수를 돌려준다.

    키를 지운 영상은 다시 후보가 되어 비식별화본이 확정된 뒤 다시 추출된다.
    그 사이에는 비식별화하지 않은 프레임을 표시하지 않도록 대표 이미지를 비워 둔다.
    키를 지운 행은 조건에 맞지 않으므로 매 주기 불러도 된다(멱등).
    """
    expr = Video.extra.op("-")(_KEY).op("-")(BUCKET_KEY).op("-")(_FROM_KEY)
    rows = (await db.execute(
        update(Video)
        .where(
            func.jsonb_exists(Video.extra, _KEY),
            thumbnail_source.stale_expr(Video.extra[_FROM_KEY].astext),
        )
        .values(extra=expr)
        .returning(Video.video_id)
        .execution_options(synchronize_session=False)
    )).all()
    if rows:
        log.warning("video_thumbnail 원본에서 뽑은 포스터 %d건을 다시 만든다", len(rows))
    return len(rows)


def fair_select(base: Select[tuple[Video]]) -> Select[tuple[Video]]:
    """후보를 프로젝트별로 번갈아 가져오는 순서로 정렬한 select를 만든다.

    이유와 방식은 `clip_thumbnail.fair_select`와 같다.

    Args:
        base: 후보 조건을 담은 select(`pending_select`). 테스트는 여기에 조건을 더해 범위를 좁힌다.
    """
    turn = (
        base.with_only_columns(
            Video.video_id,
            func.row_number().over(
                partition_by=Video.project_id, order_by=(Video.created_at, Video.video_id),
            ).label("turn"),
        )
        .subquery()
    )
    return (
        select(Video)
        .join(turn, turn.c.video_id == Video.video_id)
        .order_by(turn.c.turn, Video.created_at, Video.video_id)
    )


async def list_pending(db: AsyncSession, limit: int = BATCH_LIMIT) -> list[Video]:
    """포스터가 없는 영상을 프로젝트별로 번갈아 고른다(`fair_select`).

    후보 조건은 `pending_select`에 있다.
    """
    rows = await db.execute(fair_select(pending_select()).limit(limit))
    return list(rows.scalars())


async def _mark_failure(
    db: AsyncSession, video: Video, msg: str, *, transient: bool = False
) -> None:
    """포스터 실패를 기록한다. 일시 오류는 재시도 상한을 소모하지 않는다.

    presigned URL을 ffmpeg에 바로 넘기므로 망 상태도 실패 원인이 된다.
    일시 오류까지 세면 망 순단 세 번에 `poster_failed=true`로 확정되어 다시 시도하지 않는다.

    `extra`는 DB에서 원자적으로 병합한다. 통째로 치환하면 다른 워커가 쓴 probe 메타가 지워진다.
    """
    from api_gateway.services.media.video import merge_extra

    if transient:
        streak = int((video.extra or {}).get(_STREAK_KEY, 0)) + 1
        delay = min(_BASE_BACKOFF_S * (2 ** (streak - 1)), _MAX_BACKOFF_S)
        await merge_extra(db, video.video_id, {
            _ERROR_KEY: f"[일시] {msg}"[:200],
            _STREAK_KEY: streak,
            _RETRY_AFTER_KEY: (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(),
        })
        log.info("video_thumbnail 일시 오류 video=%s streak=%d %ds 뒤 재시도",
                 video.video_id, streak, delay)
        return

    count = int((video.extra or {}).get(_RETRY_KEY, 0)) + 1
    patch = {_ERROR_KEY: msg[:200], _RETRY_KEY: count}
    if count >= _MAX_RETRIES:
        patch[_FAILED_KEY] = "true"
    # 영구 실패가 나면 일시 오류 기록(연속 횟수·재시도 시각)을 지운다.
    # 남기면 다음 일시 오류의 백오프가 이전 연속 횟수에 이어서 계산된다.
    await merge_extra(db, video.video_id, patch, remove=(_STREAK_KEY, _RETRY_AFTER_KEY))
    # 실패로 확정(dead-letter)했을 때만 단계 이력에도 남긴다.
    # 중간 재시도까지 적으면 항목 값이 시도마다 바뀌어 현재 상태를 읽기 어렵다.
    if count >= _MAX_RETRIES:
        # 값은 타임라인 업로드 항목에 그대로 표시되므로 원문(ffmpeg 줄)을 화면 문구로 바꾼다.
        # 화면은 앞에 붙는 `실패`로 성공과 실패를 구분한다.
        said = thumbnail_reason(msg) or "사유를 확인하지 못했습니다"
        await _note_upload_run(db, video.video_id, {"thumbnail": f"실패 · {said[:80]}"})


def poster_key(video_id: UUID) -> str:
    """영상의 포스터를 올리는 키를 만든다. 포스터 주소를 서명하는 쪽이 같은 키인지 비교한다."""
    return f"{_POSTER_PREFIX}/{video_id}.jpg"


async def _process_one(db: AsyncSession, video: Video) -> bool:
    """영상 1건의 포스터를 만든다.

    영상 presign, 프레임 추출, 저장소 업로드, `poster_thumbnail_key` 기록 순서로 진행한다.
    추출할 파일은 `thumbnail_source.source_for`가 고른다. 비식별화를 쓰는 영상은 비식별화본이다.
    """
    from api_gateway.services.media.clip_thumbnail import (  # 지연 import
        FrameError,
        _extract_frame,
    )
    from api_gateway.services.media.video import _parse_s3_uri  # 지연 import로 순환을 피한다

    picked = await thumbnail_source.source_for(db, video)
    if picked is None:
        # 비식별화본이 확정되기 전이라 기다린다. 실패가 아니므로 기록하지 않는다.
        return False
    src, origin = picked
    try:
        bucket, key = _parse_s3_uri(src)
    except Exception:
        await _mark_failure(db, video, "영상 파일 위치를 읽지 못했습니다")
        await db.commit()
        return False

    await storage.ensure_loaded(db, video.extra)
    url = storage.presign_get_for(bucket=bucket, key=key)
    try:
        body = _extract_frame(url, _POSTER_TS)
    except FrameError as e:
        await _mark_failure(db, video, str(e), transient=e.transient)
        await db.commit()
        return False

    thumb_key = poster_key(video.video_id)
    try:
        # 영상의 저장소(영상에 고정 기록한 연결·버킷)에 올린다.
        store = storage.for_video(video.extra)
        storage.put_bytes(thumb_key, body, "image/jpeg", store=store)
    except Exception as e:  # noqa: BLE001 — 업로드 실패도 일시가 섞인다(스로틀·순단)
        # 원문 예외는 로그에만 남기고 사유에는 무엇이 실패했는지만 쓴다.
        # `thumbnail_reason`은 한글이 든 문장을 그대로 두므로 원문을 붙이면 화면에 표시된다.
        log.warning("video_thumbnail 업로드 실패 video=%s: %s", video.video_id, e)
        await _mark_failure(
            db, video, "썸네일을 저장소에 올리지 못했습니다", transient=is_transient_boto(e))
        await db.commit()
        return False
    from api_gateway.services.media.video import merge_extra
    await merge_extra(
        db, video.video_id, {_KEY: thumb_key, BUCKET_KEY: store.bucket, _FROM_KEY: origin},
        remove=(_ERROR_KEY, _RETRY_KEY, _FAILED_KEY, _STREAK_KEY, _RETRY_AFTER_KEY),
    )
    # 준비 이력에도 남겨 화면이 대표 이미지의 생성 여부와 실패 사유를 표시하게 한다.
    # 원본이 들어온 뒤 원본에서 추출하는 일이라 새 단계 대신 업로드 단계의 항목으로 넣는다.
    await _note_upload_run(db, video.video_id, {"thumbnail": "생성"})
    await db.commit()
    return True


async def _note_upload_run(db: AsyncSession, video_id, patch: dict) -> None:
    """업로드 단계 기록의 `detail`에 항목을 덧붙인다. 기록이 없으면 아무것도 하지 않는다.

    업로드 단계 기록이 없는 영상에는 기록을 새로 만들지 않는다.
    새로 만들면 없던 업로드 단계가 생겨 타임라인이 업로드가 지금 일어난 것으로 잘못 표시한다.
    """
    from sqlalchemy import select as _select

    from api_gateway.models import MediaStageRun

    run = (await db.scalars(
        _select(MediaStageRun)
        .where(MediaStageRun.video_id == video_id, MediaStageRun.stage == "upload")
        .order_by(MediaStageRun.id.desc()).limit(1)
    )).first()
    if run is None:
        return
    run.detail = {**(run.detail or {}), **patch}
    flag_modified(run, "detail")


async def process_pending(
    db: AsyncSession, limit: int = BATCH_LIMIT, budget_s: float = TIME_BUDGET_S,
) -> int:
    """대기 영상의 포스터를 상한과 시간 예산 안에서 만들고 성공 건수를 돌려준다. beat가 부른다.

    먼저 다시 추출할 포스터의 키를 지운다(`invalidate_unmasked`).
    다른 실행이 돌고 있으면 0을 돌려준다. 후보 행을 잠그지 않으므로 락이 없으면
    두 실행이 같은 영상의 포스터를 두 번 추출한다.
    """
    from api_gateway.services.media.clip_thumbnail import run_within_budget

    async with single_runner(LOCK_KEY) as got:
        if not got:
            return 0
        if await invalidate_unmasked(db):
            await db.commit()
        return await run_within_budget(
            await list_pending(db, limit), lambda v: _process_one(db, v),
            budget_s=budget_s, name="video_thumbnail")


async def retry_failed(db: AsyncSession, project_id: UUID, video_id: UUID, user: User) -> None:
    """실패로 확정된 대표 이미지와 클립 썸네일을 다시 만들도록 실패 표식을 지운다.

    project_admin만 부를 수 있다.
    실패 표식(`*_failed`)을 지우면 다음 주기(1분)에 워커가 다시 가져간다.
    느린 원본 하나가 응답을 수십 초 늦출 수 있어 요청 안에서 ffmpeg를 직접 돌리지 않는다.
    이 경로가 없으면 한 번 실패로 확정된 영상은 빈 썸네일로 계속 남는다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            실패로 확정된 썸네일이 없는 경우(409 `thumbnail_not_failed`).
    """
    from fastapi import HTTPException, status

    from api_gateway.models import Clip
    from api_gateway.services import audit, authz
    from api_gateway.services.media import clip as clip_service
    from api_gateway.services.media import clip_thumbnail
    from api_gateway.services.media.video import merge_extra

    video = await db.get(Video, video_id)
    if not video or video.project_id != project_id or video.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_project_admin(db, project_id, user)
    poster = (video.extra or {}).get(_FAILED_KEY) == "true"
    clip_ids = list(await db.scalars(
        select(Clip.clip_id).where(
            Clip.video_id == video_id,
            Clip.extra[clip_thumbnail.FAILED_KEY].astext == "true",
        )
    ))
    if not poster and not clip_ids:
        raise HTTPException(status.HTTP_409_CONFLICT, "thumbnail_not_failed")
    if poster:
        await merge_extra(db, video_id, {}, remove=(
            _ERROR_KEY, _RETRY_KEY, _FAILED_KEY, _STREAK_KEY, _RETRY_AFTER_KEY))
    for cid in clip_ids:
        await clip_service.merge_extra(db, cid, {}, remove=clip_thumbnail.RESET_KEYS)
    audit.emit(
        db, event_type="video.thumbnails_retried", target_kind="video", target_id=video_id,
        actor_user_id=user.user_id, project_id=project_id,
        payload={"poster": poster, "clips": len(clip_ids)},
    )
    await db.commit()


async def process_one_by_id(db: AsyncSession, video_id: UUID) -> bool:
    """영상 1건의 포스터를 바로 만든다. 등록 직후 동기로 부를 때 쓴다."""
    video = await db.get(Video, video_id)
    if video is None or video.source_uri is None:
        return False
    return await _process_one(db, video)
