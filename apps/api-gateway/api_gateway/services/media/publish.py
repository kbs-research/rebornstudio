"""재생본을 만들고 마스터를 최종 키로 옮기고 스테이징을 정리하는 publish 단계.

단계는 `transcode`(분석본 720p), `deident`(비식별화), `publish`(재생본 480p와 최종화) 순서로 돈다.
변환 단계와 나눠 두어 그 사이에 비식별화가 들어가게 한다. 비식별화는 프로젝트 설정으로 끌 수
있지만 재생본과 최종화는 항상 해야 하므로 비식별화 단계에 넣지 않는다.

- `transcode`: 분석본 마스터(720p)를 만들어 스테이징에 둔다. 원본이 MI 규격을 벗어날 때만 돈다.
- `publish`: 재생본(480p)을 만들고 마스터를 최종 버킷으로 옮긴다.
  재생본이 필요하거나 비식별화가 켜져 있을 때 돈다(`pipeline._needs`).

그래서 `h264 · 720p · mp4` 원본은 transcode를 건너뛰고 publish만 돈다.
마스터는 서버 측 S3 복사(`copy_object`)로 최종 키에 옮기고 워커는 480p 인코딩만 한다.

프로토콜은 transcode와 같다(펜싱, 리스·회수, 일시 오류의 시도 환불과 연속 상한, 영구 오류의
시도 상한).
"""

from __future__ import annotations

import logging
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import DateTime, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import Video
from api_gateway.services.joblock import lock_for_transaction
from api_gateway.services.media import resolver, stage_runs, storage
from api_gateway.services.media import video as video_service
from api_gateway.services.media.transcode import (
    _BACKOFF_BASE_S,
    _BACKOFF_MAX_S,
    _JOB_URL_TTL_S,
    _LEASE_S,
    _NOT_STARTED_NOTE,
    _PLAYBACK_CRF,
    _PLAYBACK_HEIGHT,
    _PLAYBACK_PRESET,
    _QUEUE_WAIT_S,
    _TRANSIENT_LIMIT,
    Revoke,
    _revoke,
    analysis_key,
    crash_cap_note,
    derived_bucket,
    discard_rejected,
    judge_outputs,
    master_bucket,
    master_key,
    playback_key,
    promote_staged,
    staging_key,
)

log = logging.getLogger(__name__)

_ERROR_KEY = "publish_error"  # 실패 사유를 담는 `extra` 키
_MAX_RETRIES = 3  # 영구 오류 시도 상한
_EXTEND_EVERY_S = 60  # 워커가 리스를 연장하는 간격(초)
#: publish 시도가 올리는 재생본 파일 이름. 스테이징 키의 마지막 부분이다.
_PLAYBACK_FILE = "play-480p.mp4"
#: 연속 일시 오류 횟수의 `extra` 키. 성공하거나 영구 오류가 나면 지운다.
#: 상한(`transcode._TRANSIENT_LIMIT`)과 간격은 변환과 같다.
_STREAK_KEY = "publish_transient_streak"
#: 다음 시도를 보낼 수 있는 시각(ISO)의 `extra` 키. 이 시각 전에는 투입하지 않는다.
_RETRY_AFTER_KEY = "publish_retry_after"


async def build_jobs(db: AsyncSession) -> list[dict]:
    """publish 대기 영상을 잡 페이로드로 만든다. 워커는 DB에 붙지 않는다.

    마스터가 스테이징에 생긴 뒤에 480p를 만들도록 transcode가 끝났거나 건너뛴 영상만 가져온다.
    리스는 변환과 같이 큐 대기까지 덮도록 길게 걸고(`transcode._QUEUE_WAIT_S`)
    시작 하트비트가 평소 길이로 줄인다.
    일시 오류 뒤 재시도를 기다리는 영상은 재시도 시각(`_RETRY_AFTER_KEY`)이 지나야 보낸다.
    빌더가 동시에 돌 수 있으므로 상한을 세기 전에 임계 구역을 잠근다(`lock_for_transaction`).
    """
    await lock_for_transaction(db, "reborn:dispatch:publish")
    limit = settings.transcode_max_inflight - (
        await db.scalar(
            select(func.count()).select_from(Video)
            .where(Video.publish_status.in_(("queued", "running")))
        ) or 0
    )
    if limit <= 0:
        return []
    retry_after = Video.extra[_RETRY_AFTER_KEY].astext
    videos = list(
        (await db.scalars(
            select(Video)
            .where(
                Video.publish_status == "pending",
                # 마스터를 만드는 단계가 끝난 영상만 가져온다. 마스터를 만들지 않으면 skipped다.
                Video.transcode_status.in_(("skipped", "done")),
                Video.deleted_at.is_(None),
                or_(retry_after.is_(None),
                    cast(retry_after, DateTime(timezone=True)) <= datetime.now(UTC)),
            )
            .order_by(Video.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )).all()
    )
    jobs: list[dict] = []
    now = datetime.now(UTC)
    for video in videos:
        # 이 영상의 저장소 연결·버킷이 프로세스 메모리에 없으면 서명 전에 다시 읽는다.
        await storage.ensure_loaded(db, video.extra)
        src = resolver.media_uri(video, "playback_input")
        if not src:
            video.publish_status = "failed"
            # 사유는 준비 진행 화면에 그대로 표시된다.
            await stage_runs.finish(
                db, video.video_id, "publish", ok=False,
                error="재생본을 만들 영상 파일이 없습니다.")
            await video_service.merge_extra(
                db, video.video_id, {_ERROR_KEY: "재생본을 만들 영상 파일이 없습니다."})
            continue
        in_bucket, in_key = video_service._parse_s3_uri(src)
        meta = (video.extra or {}).get("probe") or {}
        src_h = meta.get("height")
        token = uuid_mod.uuid4().hex
        video.publish_status = "running"
        await stage_runs.start(db, video.video_id, "publish", detail={"from": in_key})
        video.publish_token = token
        video.publish_lease_until = now + timedelta(seconds=_QUEUE_WAIT_S)
        video.publish_attempts = video.publish_attempts + 1
        # 시작 하트비트가 0으로 채운다.
        # 회수 때 None이면 시작하지 않은 시도로 본다(`reclaim_expired`).
        video.publish_done_seconds = None
        jobs.append({
            "video_id": str(video.video_id),
            "token": token,
            "input_url": storage.presign_get_for(
                bucket=in_bucket, key=in_key, ttl_seconds=_JOB_URL_TTL_S),
            "targets": [{
                "label": "play-480p",
                # 입력은 보통 마스터(720p)라 480p로 줄인다.
                # 원본 높이가 480 이하일 때만 스케일을 뺀다.
                "height": (_PLAYBACK_HEIGHT
                           if (src_h or _PLAYBACK_HEIGHT + 1) > _PLAYBACK_HEIGHT else None),
                "crf": _PLAYBACK_CRF,
                "preset": _PLAYBACK_PRESET,
                "threads": settings.transcode_threads,
                # 이 시도의 스테이징 키에 올리고 완료 판정이 최종 재생본 키로 옮긴다.
                # 최종 키에 바로 올리면 회수된 시도의 늦은 업로드(비식별화를 다시 돌린 영상이면
                # 마스킹 전 재생본)가 새 재생본을 덮어쓴다.
                "put_url": storage.presign_put_for(
                    bucket=derived_bucket(video),
                    key=staging_key(video.video_id, token, _PLAYBACK_FILE),
                    ttl_seconds=_JOB_URL_TTL_S),
                "content_type": "video/mp4",
            }],
            "heartbeat_every_s": _EXTEND_EVERY_S,
        })
    await db.commit()
    return jobs


async def extend_lease(
    db: AsyncSession, video_id: UUID, token: str, *, done_seconds: float | None = None,
) -> bool:
    """리스를 연장하고 처리한 초를 기록한다. 연장 기준은 transcode와 같다."""
    values: dict = {"publish_lease_until": datetime.now(UTC) + timedelta(seconds=_LEASE_S)}
    if done_seconds is not None:
        values["publish_done_seconds"] = float(done_seconds)
    row = (await db.execute(
        update(Video)
        .where(Video.video_id == video_id, Video.publish_token == token,
               Video.publish_status == "running")
        .values(**values)
        .returning(Video.video_id)
    )).first()
    await db.commit()
    return row is not None


async def complete(
    db: AsyncSession, video_id: UUID, token: str,
    *, ok: bool, error: str | None = None, transient: bool = False,
    elapsed_s: float | None = None, outputs: list | None = None,
) -> bool:
    """워커의 완료 보고를 판정한다. 보고한 토큰이 현재 토큰일 때만 받아들인다(펜싱).

    성공하면 이 시도의 스테이징 재생본을 최종 재생본 키로 옮기고, 마스터를 최종 키로 서버 측
    복사한 뒤 스테이징 파일을 지운다. 재생본을 옮기지 못하면 영구 오류로 센다.
    마스터가 없으면(transcode를 건너뛴 경우) `analysis_uri`를 비워 두고 MI는 resolver 폴백으로
    원본을 읽는다.

    재생본은 라벨러가 보는 파일이라 뒷부분이 잘리면 그 구간의 클립이 재생되지 않으므로
    원본과 비교한다(`transcode.judge_outputs`). 어긋나면 영구 오류, 검사 시간 초과는 일시 오류로
    처리하고 통과하지 못한 재생본은 지운다. 상한에 닿은 영상은 `transcode.retry`로 되살린다.
    통과한 측정값은 `extra["publish_output"]`에 병합한다. `outputs`가 없으면 검사 없이 받는다.
    """
    video = await db.get(Video, video_id)
    if video is None:
        return False

    # 산출물 버킷은 행을 갱신하기 전에 읽어 둔다. 아래 갱신·병합 뒤에 `extra`를 읽으면
    # 만료된 속성을 동기 경로에서 다시 읽게 되어 실패한다.
    await storage.ensure_loaded(db, video.extra)
    out_bucket = derived_bucket(video)
    meta = (video.extra or {}).get("probe") or {}
    verdict = judge_outputs(meta, outputs, name="재생본") if ok else None
    if verdict is not None and verdict.cause is not None:
        # 검사를 통과하지 못하면 워커의 성공 보고를 실패로 처리한다.
        # 검사 시간 초과만 일시 오류다(`judge_outputs`).
        ok, transient, error = False, verdict.transient, verdict.error
    measured = verdict.measured if verdict is not None else None
    # 산출 검사 위반은 위반 코드(`cause`, 집계용)와 수치(`measured`, 원인 추적용)를 이력에 싣는다.
    check = ({"cause": verdict.cause, "measured": measured}
             if verdict is not None and verdict.cause else {})

    if ok:
        row = (await db.execute(
            update(Video)
            .where(Video.video_id == video_id, Video.publish_token == token,
                   Video.publish_status == "running")
            .values(publish_status="done", publish_token=None, publish_lease_until=None)
            .returning(Video.video_id)
        )).first()
        if row:
            try:
                # 이 시도가 올린 재생본만 최종 키로 옮긴다.
                promote_staged(out_bucket, staging_key(video_id, token, _PLAYBACK_FILE),
                               playback_key(video_id))
            except Exception as exc:  # noqa: BLE001 — 옮기지 못한 결과를 성공으로 접지 않는다
                # 이유는 `transcode.complete_transcode`의 마스터 승격 실패와 같다.
                await db.rollback()
                log.error("publish 재생본 승격 실패 video=%s: %s: %s",
                          video_id, type(exc).__name__, exc)
                return await complete(
                    db, video_id, token, ok=False,
                    error="결과 파일을 저장소에 올리지 못했습니다", transient=False)
            await db.refresh(video)
            detail = await _finalize_master(db, video)
            # 원본은 여기서 정리하지 않는다. publish 뒤에도 MI 등록·분석이 남아 있고,
            # 원본이 이미 MI 규격이면 `resolver`가 원본을 그대로 MI에 넘긴다.
            # 정리는 `retention.dispose_finished_sources`가 한다.
            await video_service.merge_extra(db, video_id, {
                "transcode": {
                    "playback_key": playback_key(video_id),
                    "bucket": out_bucket,
                    "publish_elapsed_s": elapsed_s,
                    **detail,
                },
                # 재생본 측정값. 병합이 최상위 키 단위라서 `transcode`와 따로 둔다.
                **({"publish_output": measured} if measured else {}),
            }, remove=(_ERROR_KEY, _STREAK_KEY, _RETRY_AFTER_KEY))
            await stage_runs.finish(
                db, video_id, "publish", ok=True,
                detail={"playback_height": _PLAYBACK_HEIGHT,
                        "playback_key": playback_key(video_id), **detail,
                        # 산출 측정값과 원본 대비 차이. 화면의 검사 항목이 읽는다.
                        **({"measured": measured} if measured else {})},
            )
        await db.commit()
        if not row:
            log.warning("publish 펜싱 거부 — 낡은 완료 보고 폐기 video=%s", video_id)
            # 낡은 시도가 올린 재생본을 지운다. 이유는 `transcode.complete_transcode`와 같다.
            discard_rejected(out_bucket, staging_key(video_id, token, _PLAYBACK_FILE))
        return row is not None

    # 단계 이력에는 `[일시]` 표식을 붙이지 않는다. 일시 여부는 `detail.transient`에 싣는다
    # (`transcode.complete_transcode`와 같은 규칙).
    said = (error or "사유를 받지 못했습니다.")[:400]
    msg = f"{'[일시] ' if transient else ''}{said}"
    # 일시 오류는 시도를 소모하지 않고 연속 횟수를 센다(`transcode.complete_transcode`와 같은 규칙).
    streak = int((video.extra or {}).get(_STREAK_KEY) or 0) + 1 if transient else 0
    capped = transient and streak >= _TRANSIENT_LIMIT
    if capped:
        msg = f"{said.rstrip('.')}. 일시 오류가 {streak}회 이어져 자동 재시도를 멈췄습니다."
    if (not transient and video.publish_attempts >= _MAX_RETRIES) or capped:
        row = (await db.execute(
            update(Video)
            .where(Video.video_id == video_id, Video.publish_token == token,
                   Video.publish_status == "running")
            .values(publish_status="failed", publish_token=None, publish_lease_until=None)
            .returning(Video.video_id)
        )).first()
        if row:
            await stage_runs.finish(db, video_id, "publish", ok=False, error=msg,
                                    detail=check or None)
            await video_service.merge_extra(
                db, video_id, {_ERROR_KEY: msg}, remove=(_STREAK_KEY, _RETRY_AFTER_KEY))
            if check:
                # 검사를 통과하지 못한 재생본이 이 시도의 스테이징 키에 남아 있다.
                discard_rejected(out_bucket, staging_key(video_id, token, _PLAYBACK_FILE))
            log.error("publish 확정 실패(%s %d회) video=%s: %s",
                      "일시 오류 연속" if capped else "시도",
                      streak if capped else video.publish_attempts, video_id, msg)
        await db.commit()
        return False

    row = (await db.execute(
        update(Video)
        .where(Video.video_id == video_id, Video.publish_token == token,
               Video.publish_status == "running")
        .values(
            publish_status="pending", publish_token=None, publish_lease_until=None,
            # 일시 오류는 실행 시점의 문제라서 시도 횟수를 되돌린다.
            publish_attempts=Video.publish_attempts - (1 if transient else 0),
        )
        .returning(Video.video_id)
    )).first()
    if row:
        if transient:
            # 만료된 URL이나 저장소 장애가 이어지는 동안
            # 연속 횟수에 따라 간격을 늘린다(지수 백오프).
            delay = min(_BACKOFF_BASE_S * 2 ** (streak - 1), _BACKOFF_MAX_S)
            retry_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
            await stage_runs.finish(
                db, video_id, "publish", ok=False, error=said,
                detail={"transient": True, "streak": streak, "retry_after": retry_at, **check})
            await video_service.merge_extra(db, video_id, {
                _ERROR_KEY: msg, _STREAK_KEY: streak, _RETRY_AFTER_KEY: retry_at})
        else:
            # 영구 오류가 나면 일시 오류의 연속 횟수를 초기화한다.
            await stage_runs.finish(
                db, video_id, "publish", ok=False, error=said,
                detail={"transient": False, **check})
            await video_service.merge_extra(
                db, video_id, {_ERROR_KEY: msg}, remove=(_STREAK_KEY, _RETRY_AFTER_KEY))
        if check:
            discard_rejected(out_bucket, staging_key(video_id, token, _PLAYBACK_FILE))
        log.warning("publish %s — pending 복귀 video=%s: %s",
                    "일시 오류" if transient else "재시도", video_id, msg)
    await db.commit()
    return False


async def reset_failed(db: AsyncSession, video: Video) -> str:
    """`failed`로 확정된 재생본 변환을 pending으로 되돌린다.

    `transcode.retry`가 부른다. 화면은 두 인코딩을 `변환 재시도` 하나로 표시하고 준비 단계
    분류에서도 publish는 변환에 속한다(`video._prep_stage_case`). 재생본 쪽 열은 이 모듈이
    다루므로 되돌리기만 여기서 하고, 권한·상태 확인과 감사 기록은 호출자가 한다.

    시도 횟수는 0으로 되돌린다(`transcode.retry`와 같은 이유).

    Returns:
        str: 감사 기록에 남길 되돌리기 전 사유(최대 200자).
    """
    before = ((video.extra or {}).get(_ERROR_KEY) or "")[:200]
    video.publish_status = "pending"
    video.publish_attempts = 0
    video.publish_token = None
    video.publish_lease_until = None
    # 진행률의 분자도 비운다. 남기면 새 시도가 시작하자마자 지난 시도의 퍼센트를 표시한다.
    video.publish_done_seconds = None
    await db.flush()
    # 실패 기록과 일시 오류 연속 횟수·재시도 시각을 지운다. 남기면 목록이 실패 사유를 계속
    # 표시하고 다시 넣은 영상이 일시 오류 한 번에 곧바로 멈출 수 있다.
    await video_service.merge_extra(
        db, video.video_id, {}, remove=(_ERROR_KEY, _STREAK_KEY, _RETRY_AFTER_KEY))
    return before


async def _dispose_source(db: AsyncSession, video: Video) -> dict:
    """반입 스테이징의 원본을 정리한다. 방법은 프로젝트 정책이 정한다.

    - `retain_original`이 true면 보관 프리픽스로 옮긴다.
      순서는 복사, `source_uri`를 옮긴 위치로 바꿔 커밋, 원래 키 삭제다.
      원래 키를 커밋보다 먼저 지우면 그 사이 워커가 멈출 때 행이 지워진 키를 가리킨다.
      삭제가 실패하면 원래 키에 사본이 남지만 행은 보관본을 가리키므로 다시 처리할 수 있다.
      원래 키가 없고 보관본이 있으면 앞 실행이 복사한 뒤 멈춘 경우로 보고 `source_uri`만 바꾼다.
    - false면 지운다. 원본이 어디에도 남지 않는다. 다시 처리하려면 다시 올려야 한다.

    지울 때도 출처 기록이자 감사 근거인 `source_uri`는 그대로 둔다.
    파일이 남아 있는지는 `source_retained`가 나타낸다.

    키가 `intake/{video_id}/`로 시작할 때만 정리한다. 버킷 등록 영상(반입 스테이징을 거치지 않은
    영상)의 원본은 원래 버킷에 있던 파일이라 이 서비스가 지우지 않는다.
    """
    from api_gateway.models import Project
    from api_gateway.services.media.video import archive_key

    uri = video.source_uri or ""
    bucket, key = (video_service._parse_s3_uri(uri) if uri.startswith(("s3://", "ncp://"))
                   else ("", ""))
    if not key.startswith(f"intake/{video.video_id}/"):
        # 이 영상의 업로드 원본이 아니므로 건드리지 않는다. `intake/`만 보면 다른 영상의
        # 원본 키로 등록한 행이 그 영상의 원본을 지우거나 옮긴다.
        # 원본 처리는 되돌릴 수 없어서 아무것도 하지 않은 결과("유지")도 기록한다.
        return {"source": "유지"}

    retain = bool(await db.scalar(
        select(Project.retain_original).where(Project.project_id == video.project_id)))
    if retain:
        dst = archive_key(video.video_id)
        try:
            storage.copy_object(bucket=bucket, src_key=key, dst_key=dst)
        except Exception:
            already = (storage.object_exists(bucket=bucket, key=key) is False
                       and storage.object_exists(bucket=bucket, key=dst) is True)
            if not already:
                raise
            log.info("원본 보관: 업로드 키 없이 보관본만 있음 video=%s", video.video_id)
        video.source_uri = f"s3://{bucket}/{dst}"
        await db.commit()
        try:
            storage.delete_object(bucket=bucket, key=key)
        except Exception:  # noqa: BLE001 — 행은 이미 보관본을 가리킨다
            log.warning("원본 보관 뒤 업로드 키 삭제 실패 video=%s key=%s",
                        video.video_id, key, exc_info=True)
        return {"source": "보관"}
    storage.delete_object(bucket=bucket, key=key)
    video.source_retained = False
    return {"source": "삭제"}


async def _finalize_master(db: AsyncSession, video: Video) -> dict:
    """분석본 마스터를 서버 측 복사로 최종 키에 옮긴다.

    복사가 끝나면 스테이징 파일을 지운다. 남겨 두면 같은 파일에 두 번 과금된다.
    마스터가 없으면(transcode를 건너뛴 경우) 아무것도 하지 않고 MI는 원본을 읽는다(resolver 폴백).
    """
    bucket, mkey = master_bucket(video), master_key(video.video_id)
    if not storage.object_exists(bucket=bucket, key=mkey):
        return {}
    final = analysis_key(video.video_id)
    storage.copy_object(bucket=bucket, src_key=mkey, dst_key=final)
    storage.delete_object(bucket=bucket, key=mkey)
    video.analysis_uri = f"s3://{bucket}/{final}"
    return {"analysis_key": final}


async def reclaim_expired(db: AsyncSession, *, revoke: Revoke | None = None) -> int:
    """리스가 만료된 잡을 회수한다. 워커가 비정상 종료돼도 처리는 지연될 뿐 멈추지 않는다.

    시작 하트비트가 온 뒤 회수된 시도는 시도로 세고 상한에 닿으면 `failed`로 확정한다.
    시작하지 못한 시도는 시도를 되돌린다(`transcode.reclaim_expired`와 같은 규칙).
    회수한 시도의 단계 이력은 일시 오류로 닫는다(`deident.reclaim_expired`와 같은 규칙).
    이전 토큰은 커밋한 뒤 `revoke`로 넘겨 큐에 남은 이전 메시지를 버리게 한다.
    """
    now = datetime.now(UTC)
    # 지울 토큰을 먼저 읽는다. UPDATE의 RETURNING은 바뀐 값(빈 토큰)을 돌려준다.
    rows = (await db.execute(
        select(Video.video_id, Video.publish_token, Video.publish_done_seconds,
               Video.publish_attempts)
        .where(Video.publish_status == "running", Video.publish_lease_until < now)
        .with_for_update(skip_locked=True)
    )).all()
    for vid, _token, done, attempts in rows:
        if done is not None and attempts >= _MAX_RETRIES:
            note = crash_cap_note(attempts)
            await db.execute(
                update(Video).where(Video.video_id == vid)
                .values(publish_status="failed", publish_token=None, publish_lease_until=None))
            await stage_runs.finish(db, vid, "publish", ok=False, error=note,
                                    detail={"transient": False, "reclaimed": True})
            await video_service.merge_extra(db, vid, {_ERROR_KEY: note})
            log.error("publish 확정 실패(시작 뒤 회수 %d회) video=%s", attempts, vid)
            continue
        await db.execute(
            update(Video).where(Video.video_id == vid)
            .values(publish_status="pending", publish_token=None, publish_lease_until=None,
                    publish_attempts=Video.publish_attempts - (1 if done is None else 0)))
        # 사유 문장은 장면 분할·비식별화 회수와 같다. 시작 하트비트가 오지 않은 시도는
        # 처리 서버가 잡을 가져가지 못한 경우로 본다.
        await stage_runs.finish(
            db, vid, "publish", ok=False,
            error=_NOT_STARTED_NOTE if done is None else "처리 서버 응답이 끊겼습니다.",
            detail={"transient": True, "reclaimed": True})
    await db.commit()
    _revoke(revoke, [t for _, t, _, _ in rows if t])
    if rows:
        log.warning("publish 리스 만료 회수 %d건", len(rows))
    return len(rows)
