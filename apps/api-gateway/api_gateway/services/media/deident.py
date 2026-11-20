"""비식별화(얼굴·번호판 마스킹) 단계의 DB 쪽 처리.

파이프라인 순서는 모델과 따로 고정한다. 모델을 바꿀 때는 모델 어댑터 하나만 붙이고
순서는 다시 검증하지 않는다.
잡은 트랜스코딩 잡과 같은 프로토콜(리스와 회수, 펜싱, 미디어 시간 기준 진행률)을 쓴다.

비식별화를 켰는데 처리하지 못하면 파이프라인을 멈춘다.
그대로 통과시키면 마스킹하지 않은 영상이 오류 없이 다음 단계로 넘어간다.

- `skipped`: 이 프로젝트가 비식별화를 쓰지 않는 경우. publish로 진행한다(raw 마스터).
  대기 중에 프로젝트가 끈 영상도 이 상태로 넘긴다(`skip_disabled`).
  끈 프로젝트에서 실패로 확정된 영상은 관리자가 고를 때만 넘긴다(`skip_failed`).
- `failed`: 쓰기로 했는데 처리하지 못한 경우. 파이프라인이 멈추고
  publish와 MI 어느 쪽으로도 보내지 않는다.

두 상태를 같은 값으로 두면 가리지 않은 얼굴이 클라우드 분석기로 나간다.

MI는 비식별 사본만 읽는다. 그래서 비식별화를 켜면 원본이 이미 MI 규격이어도 늘 마스터를
만들고, publish가 그 마스터를 분석본 위치로 확정한다.

아무것도 가리지 않은 모델도 정상으로 보이는 파일을 내므로 결과 파일만으로는 성공과 실패를
구분할 수 없다. 그래서 모델 이름·버전·검출 수를 이력에 남기고 화면에 그대로 표시한다.

모델은 긴 작업 워커(worker-gpu) 안에서 돈다. 워커가 `DEIDENT_MODEL`의 어댑터를 실행하고,
비어 있으면 내장 비식별화 모듈(`essential-yunet`)을 쓴다(`reborn_transcode.deident`).
잡에는 `input_url`·`put_url`·`coordinates_put_url`과 토큰이 있어 워커에 저장소 자격증명이
필요 없다.

`dispatch_deidents`는 전역 동시 처리 상한(`DEIDENT_MAX_INFLIGHT`, 기본 1)까지만 보낸다.
계약과 매핑은 `queues.md`의 `deident_one` 스키마에 있다.
"""

from __future__ import annotations

import logging
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import DateTime, case, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import AnalysisJob, Clip, Project, User, Video
from api_gateway.services import audit, authz
from api_gateway.services.deident_gap import deident_gap, reached_deident
from api_gateway.services.joblock import lock_for_transaction
from api_gateway.services.media import resolver, stage_runs, storage
from api_gateway.services.media import video as video_service
from api_gateway.services.media.transcode import (
    _JOB_URL_TTL_S,
    _LEASE_S,
    _QUEUE_WAIT_S,
    Revoke,
    _revoke,
    crash_cap_note,
    derived_bucket,
    master_key,
    promote_staged,
    staging_key,
)

log = logging.getLogger(__name__)

_ERROR_KEY = "deident_error"  # 실패 사유를 담는 `extra` 키
_MAX_RETRIES = 3  # 영구 오류 시도 상한
_EXTEND_EVERY_S = 60  # 워커가 리스를 연장하는 간격(초)
#: 회수한 시도의 사유. 준비 타임라인의 시도 행에 그대로 표시되므로 `리스`·`워커` 같은
#: 내부 용어를 넣지 않는다. 장면 분할 회수(`scene_split._RECLAIM_NOTE`)와 같은 문장이다.
_RECLAIM_NOTE = "처리 서버 응답이 끊겼습니다."
#: 회수 표식의 `extra` 키. 값은 `{token, dispatched_at, reclaimed_at}`이고 다음 투입 때 지운다.
_RECLAIMED_KEY = "deident_reclaimed"
#: 연속 일시 오류(입력 다운로드·업로드 실패·시간 초과)의 상한. 이 값에 이르면 실패로 확정한다.
#: 재시도 간격은 `_BACKOFF_BASE_S`부터 두 배씩 늘고 `_BACKOFF_MAX_S`에서 멈춘다.
#: 확정까지 기다리는 시간의 합은 sum(min(60·2^(k-1), 1800), k=1..9) = 9,060초(약 2.5시간)다.
#: 저장소 장애나 워커 재배포가 이어지는 시간보다 길게 둔다.
_TRANSIENT_LIMIT = 10
_BACKOFF_BASE_S = 60
_BACKOFF_MAX_S = 1800
#: 연속 일시 오류 횟수의 `extra` 키. 성공하거나 영구 오류가 나면 지운다.
_STREAK_KEY = "deident_transient_streak"
#: 다음 시도를 보낼 수 있는 시각(ISO)의 `extra` 키. 이 시각 전에는 투입하지 않는다.
_RETRY_AFTER_KEY = "deident_retry_after"
#: 시작 하트비트를 보낸 시도의 토큰을 담는 `extra` 키. 회수가 시작한 시도인지 가를 때 쓴다.
#: 비식별화 하트비트는 진행 초를 싣지 않아 변환처럼 진행 초로 시작 여부를 가를 수 없다.
_STARTED_KEY = "deident_started"


def _projects(enabled: bool):
    """비식별화를 켠(또는 끈) 프로젝트 id의 서브쿼리를 만든다."""
    return select(Project.project_id).where(Project.deident_enabled.is_(enabled))


def _ready() -> tuple:
    """잡으로 보낼 수 있는 대기 영상의 조건을 만든다.

    마스터를 만드는 단계가 끝났고, 재시도 시각이 지났고, 프로젝트가 지금 비식별화를 켠 영상이다.
    대기 중에 비식별화를 끈 프로젝트의 영상은 보내지 않고 `skip_disabled`가 건너뜀으로 넘긴다.
    재시도 시각을 현재 시각과 비교하므로 부를 때마다 새로 만든다.
    """
    retry_after = Video.extra[_RETRY_AFTER_KEY].astext
    return (
        Video.deident_status == "pending",
        Video.transcode_status.in_(("skipped", "done")),
        Video.deleted_at.is_(None),
        Video.project_id.in_(_projects(True)),
        or_(retry_after.is_(None),
            cast(retry_after, DateTime(timezone=True)) <= datetime.now(UTC)),
    )


async def skip_disabled(db: AsyncSession) -> int:
    """비식별화를 끈 프로젝트의 대기 영상을 건너뜀으로 넘기고 다음 단계를 연다. 넘긴 수를 돌려준다.

    켜 둔 동안 대기에 들어간 영상(첫 투입 전·자동 재시도 대기·회수 뒤)은 끈 뒤에도 대기에 남는다.
    `_ready`는 이 영상을 보내지 않으므로 여기서 넘기지 않으면 계속 "변환 중"에 머문다.
    넘긴 영상은 처음부터 비식별화를 쓰지 않는 프로젝트의 영상과 같은 `skipped`가 되어
    "쓰지 않음"으로 표시된다(`deident_gap`). 프로젝트가 다시 켜면 재시도로 마스킹할 수 있다.

    처리 중(`queued`·`running`)인 영상은 바꾸지 않는다. 일시 오류로 대기에 돌아오면
    다음 주기에 넘긴다. 끈 프로젝트의 영상은 다시 보내지 않으므로 재시도 시각은 보지 않는다.

    넘길 때마다 수행자 없이 `video.deident_skipped` 감사 기록을 남긴다.
    """
    rows = (await db.execute(
        update(Video)
        .where(Video.deident_status == "pending", Video.deident_token.is_(None),
               Video.deleted_at.is_(None), Video.project_id.in_(_projects(False)))
        .values(deident_status="skipped", deident_lease_until=None)
        .returning(Video.video_id, Video.project_id, Video.deident_attempts, Video.extra)
    )).all()
    await _settle_skipped(db, rows, event_type="video.deident_skipped", reason="project_disabled")
    if rows:
        log.info("deident 꺼진 프로젝트의 대기 영상 %d건을 건너뜀으로 넘김", len(rows))
    return len(rows)


async def _settle_skipped(
    db: AsyncSession, rows, *, event_type: str, reason: str, actor_user_id: UUID | None = None,
) -> None:
    """건너뜀으로 넘긴 영상의 실패 기록을 지우고, 감사 기록을 남기고, 다음 단계(재생본)를 연다.

    배치(`skip_disabled`)와 관리자의 건너뛰기(`skip_failed`)가 같이 쓴다.
    둘이 따로 정리하면 한쪽에만 실패 사유가 남아 화면이 "지금도 실패 중"으로 표시한다.
    상태 전이(`skipped`)는 부르는 쪽의 조건부 UPDATE가 하고 이 함수는 그 결과 행만 받는다.

    감사 기록의 `attempts`는 워커에 실제로 보낸 횟수로, 단계 이력에서 센다
    (`stage_runs.attempt_counts`). `deident_attempts`는 상한 판정용 수라서 일시 오류와 회수 때
    되돌리므로 실제 횟수보다 작을 수 있다. 이 값은 `counted_attempts`로 따로 싣는다.

    Args:
        rows: UPDATE ... RETURNING이 돌려준 `(video_id, project_id, deident_attempts, extra)` 행.
        event_type: 남길 감사 이벤트 종류.
        reason: 감사 기록의 사유 코드.
        actor_user_id: 넘긴 사용자. 배치가 넘기면 `None`.
    """
    from api_gateway.services.media.pipeline import open_next
    for vid, project_id, counted, extra in rows:
        before = ((extra or {}).get(_ERROR_KEY) or "")[:200]
        sent = (await stage_runs.attempt_counts(db, vid)).get("deident", 0)
        await video_service.merge_extra(
            db, vid, {}, remove=(_ERROR_KEY, _STREAK_KEY, _RETRY_AFTER_KEY, _RECLAIMED_KEY))
        audit.emit(
            db, event_type=event_type, target_kind="video", target_id=vid,
            actor_user_id=actor_user_id, project_id=project_id,
            payload={"reason": reason, "attempts": sent, "counted_attempts": counted,
                     "previous_error": before},
        )
        # 세션에 로드된 영상 객체에는 UPDATE 전 값이 남아 있어 다음 단계 판정 전에 다시 읽는다.
        video = await db.get(Video, vid, populate_existing=True)
        await open_next(db, video)


async def skip_failed(db: AsyncSession, project_id: UUID, video_id: UUID, user: User) -> None:
    """비식별화를 끈 프로젝트에서 실패로 확정된 영상을 건너뜀으로 넘기고 재생본 단계를 연다.

    project_admin이 부른다.
    `skip_disabled`는 실패로 확정된 영상을 넘기지 않고, 끈 프로젝트에서는 `retry`도 받지 않는다.
    사람이 실패 사유(원본 손상 등)를 보고 판단하도록 이 요청으로만 재생본 단계로 보낸다.

    비식별화를 켠 프로젝트에서는 받지 않는다. 넘기면 마스킹하지 않은 원본이 분석으로 나간다.
    넘긴 영상은 `skip_disabled`가 넘긴 영상과 같은 상태가 된다.
    상태 조건을 UPDATE의 WHERE에 같이 걸어 확인과 전이 사이에 상태가 바뀐 영상은 넘기지 않는다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            프로젝트가 비식별화를 쓰는 경우(409 `deident_enabled`),
            비식별화가 실패로 확정된 영상이 아닌 경우(409 `deident_not_failed`).
    """
    video = await db.get(Video, video_id)
    if not video or video.project_id != project_id or video.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_project_admin(db, project_id, user)
    enabled = await db.scalar(
        select(Project.deident_enabled).where(Project.project_id == project_id)
    )
    if enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "deident_enabled")
    rows = (await db.execute(
        update(Video)
        .where(Video.video_id == video_id, Video.deident_status == "failed",
               Video.deleted_at.is_(None), Video.project_id.in_(_projects(False)))
        .values(deident_status="skipped", deident_token=None, deident_lease_until=None)
        .returning(Video.video_id, Video.project_id, Video.deident_attempts, Video.extra)
        .execution_options(synchronize_session=False)
    )).all()
    if not rows:
        raise HTTPException(status.HTTP_409_CONFLICT, "deident_not_failed")
    await _settle_skipped(db, rows, event_type="video.deident_skip_manual", reason="manual",
                          actor_user_id=user.user_id)
    await db.commit()


def deident_key(video_id: UUID, token: str) -> str:
    """시도 하나의 비식별화 산출 스테이징 키를 만든다. 입력(마스터)과 다른 키여야 한다.

    입력 키에 덮어쓰면 중간에 중단됐을 때 원본도 산출도 아닌 파일이 남고,
    재시도가 이미 마스킹된 파일을 다시 마스킹한다.
    키에 시도의 토큰이 들어가므로(`transcode.staging_key`) 회수된 시도의 늦은 업로드가
    현재 시도의 산출을 덮어쓰지 못한다.
    """
    return staging_key(video_id, token, "analysis-720p.deid.mp4")


def coordinates_staging_key(video_id: UUID, token: str) -> str:
    """시도 하나의 얼굴 좌표 JSON 스테이징 키를 만든다. 성공하면 `coordinates_key`로 옮긴다.

    최종 키에 바로 올리면 회수된 시도의 늦은 업로드가 현재 시도의 좌표를 덮어써
    마스터의 가림 위치와 좌표 파일이 어긋난다.
    """
    return staging_key(video_id, token, "analysis-720p.faces.json")


def coordinates_key(video_id: UUID) -> str:
    """비식별화가 실제로 가린 얼굴 박스 JSON의 최종 키를 만든다.

    좌표는 분석본 720p가 되는 비식별 산출의 픽셀 기준이라
    분석본(`transcode.analysis_key`) 옆에 둔다.
    워커는 스테이징 키(`coordinates_staging_key`)에 올리고 성공 판정이 이 키로 옮긴다.
    """
    return f"transcoded/720p/{video_id}.faces.json"


def _coordinates_record(bucket: str, video_id: UUID, report: dict | None) -> dict | None:
    """워커가 좌표 JSON을 올렸다고 보고했을 때만 저장할 좌표 기록을 만든다.

    키는 `coordinates_key`로 다시 만든다. 보고가 없거나 `uploaded`가 참이 아니면 `None`이다.
    검증용 어댑터(`passthrough`)처럼 좌표를 만들지 않는 경우에 없는 파일을 가리키지 않게 한다.
    """
    d = (report or {}).get("coordinates")
    if not isinstance(d, dict) or d.get("uploaded") is not True:
        return None
    return {
        "bucket": bucket,
        "key": coordinates_key(video_id),
        "schema_version": d.get("schema_version"),
        "coordinate_space": d.get("coordinate_space"),
    }


def _modified_at(bucket: str, key: str) -> str | None:
    """객체의 수정 시각을 ISO 문자열로 돌려준다. 객체나 수정 시각 정보가 없으면 `None`이다."""
    info = storage.head_in(bucket=bucket, key=key)
    if info is None or info.last_modified is None:
        return None
    return info.last_modified.isoformat()


def _staged_evidence(
    bucket: str, video_id: UUID, token: str, in_bucket: str, in_key: str, *, coordinates: bool,
) -> dict | None:
    """스테이징에 남은 비식별 산출과 입력의 저장 시각을 모은다. 늦은 완료 보고의 판정이 쓴다.

    워커는 presigned URL만 있어 저장소를 조회할 수 없으므로 서버가 산출이 어느 시도에서
    왔는지 시각으로 판정한다(`_made_after`). 산출은 `token` 시도의 스테이징 키에서 찾는다.

    산출 mp4가 없거나(업로드 전이거나 이미 승격됨), 받기로 한 좌표 JSON이 없거나,
    조회가 실패하면 `None`이다. 근거가 없으면 늦은 보고를 이어받지 않는다.

    Returns:
        `{input_modified_at, output_modified_at, coordinates_modified_at?}`. 값은 ISO 시각이다.
    """
    try:
        output = _modified_at(bucket, deident_key(video_id, token))
        if output is None:
            return None
        source = _modified_at(in_bucket, in_key)
        if source is None:
            return None
        evidence = {"input_modified_at": source, "output_modified_at": output}
        if coordinates:
            faces = _modified_at(bucket, coordinates_staging_key(video_id, token))
            if faces is None:
                return None
            evidence["coordinates_modified_at"] = faces
    except Exception as exc:  # noqa: BLE001 — 근거가 없으면 이어받지 않을 뿐이다
        log.warning("deident 앞선 산출 조회 실패 video=%s: %s: %s",
                    video_id, type(exc).__name__, exc)
        return None
    return evidence


async def _pick(db: AsyncSession) -> list[Video]:
    """전역 동시 처리 상한까지 남은 수만큼 대기 영상을 골라 잠근다.

    회수되어 돌아온 영상을 먼저 고르고, 나머지는 등록 순서대로 고른다.
    빌더가 동시에 돌 수 있으므로 상한을 세기 전에 임계 구역을 잠근다(`lock_for_transaction`).
    잠금은 `build_jobs`가 커밋할 때 풀린다.
    """
    await lock_for_transaction(db, "reborn:dispatch:deident")
    running = await db.scalar(
        select(func.count()).select_from(Video)
        .where(Video.deident_status.in_(("queued", "running")))
    ) or 0
    free = settings.deident_max_inflight - running
    if free <= 0:
        return []
    return list((await db.scalars(
        select(Video)
        .where(*_ready())
        .order_by(_reclaimed_first(), Video.created_at)
        .limit(free)
        .with_for_update(skip_locked=True)
    )).all())


def _reclaimed_first():
    """회수되어 대기로 돌아온 영상을 앞에 두는 정렬 식을 만든다.

    회수된 영상은 이미 한 번 차례를 받고 리스가 끝날 때까지 기다렸으므로
    대기열 끝으로 보내면 워커 재배포 한 번에 대기 시간이 두 배 이상으로 늘어난다.
    """
    return case((func.jsonb_exists(Video.extra, _RECLAIMED_KEY), 0), else_=1)


async def build_jobs(db: AsyncSession) -> list[dict]:
    """비식별화 대기 영상을 잡 페이로드로 만든다.

    워커는 DB에 접근하지 않으므로 처리에 필요한 값을 모두 잡에 싣는다.
    동시 처리 상한은 전역 하나(`deident_max_inflight`)이고 고르는 순서는 `_pick`이 정한다.
    고르기 전에 비식별화를 끈 프로젝트의 대기 영상을 건너뜀으로 넘긴다(`skip_disabled`).

    잡에는 늘 얼굴 좌표 JSON의 업로드 주소(`coordinates_put_url`)를 싣는다.
    서버는 워커가 올렸다고 보고한 경우에만 좌표를 기록한다.
    """
    await skip_disabled(db)
    videos = await _pick(db)
    jobs: list[dict] = []
    now = datetime.now(UTC)
    for video in videos:
        # 이 프로세스에 이 영상의 저장소 연결·버킷 정보가 없으면 서명 전에 읽어 온다.
        await storage.ensure_loaded(db, video.extra)
        src = resolver.media_uri(video, "deident_input")
        if not src:
            # 비식별화를 켜 놓고 입력이 없으면 실패다. 사유는 화면에 그대로 표시된다.
            video.deident_status = "failed"
            await stage_runs.finish(
                db, video.video_id, "deident", ok=False, error="비식별화할 영상 파일이 없습니다.")
            await video_service.merge_extra(
                db, video.video_id, {_ERROR_KEY: "비식별화할 영상 파일이 없습니다."})
            continue
        in_bucket, in_key = video_service._parse_s3_uri(src)
        token = uuid_mod.uuid4().hex
        video.deident_status = "running"
        await stage_runs.start(db, video.video_id, "deident", detail={"from": in_key})
        reclaimed = _RECLAIMED_KEY in (video.extra or {})
        video.deident_token = token
        # 큐 대기 시간까지 덮는 리스(`transcode._QUEUE_WAIT_S`). 시작 하트비트가 평소 길이로 줄인다.
        video.deident_lease_until = now + timedelta(seconds=_QUEUE_WAIT_S)
        video.deident_attempts = video.deident_attempts + 1
        job = {
            "video_id": str(video.video_id),
            "token": token,
            "input_url": storage.presign_get_for(
                bucket=in_bucket, key=in_key, ttl_seconds=_JOB_URL_TTL_S),
            "put_url": storage.presign_put_for(
                bucket=derived_bucket(video), key=deident_key(video.video_id, token),
                ttl_seconds=_JOB_URL_TTL_S),
            "content_type": "video/mp4",
            # 실제로 가린 얼굴 박스의 프레임 단위 JSON.
            # Content-Type이 서명에 들어가므로 워커가 올리는 형식과 같아야 한다.
            "coordinates_put_url": storage.presign_put_for(
                bucket=derived_bucket(video), key=coordinates_staging_key(video.video_id, token),
                content_type="application/json", ttl_seconds=_JOB_URL_TTL_S),
            "heartbeat_every_s": _EXTEND_EVERY_S,
        }
        if reclaimed:
            # 새 시도를 보냈으므로 회수 표식을 지워 옛 토큰의 늦은 보고를 이어받지 않게 한다.
            # 병합 뒤에는 `video.extra`가 만료되므로 이 영상의 마지막 처리로 둔다.
            await video_service.merge_extra(db, video.video_id, {}, remove=(_RECLAIMED_KEY,))
        jobs.append(job)
    await db.commit()
    return jobs


def _parse_at(value: object) -> datetime | None:
    """ISO 시각 문자열을 시간대가 있는 시각으로 읽는다. 읽지 못하거나 시간대가 없으면 `None`이다."""
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo is not None else None


def _made_after(staged: dict, dispatched_at: datetime) -> bool:
    """스테이징 산출이 `dispatched_at` 뒤에 올라왔고 입력은 그 전부터 있었으면 참이다.

    좌표 시각이 있으면 좌표 JSON도 `dispatched_at` 뒤에 올라왔어야 참이다.
    산출 키에는 시도의 토큰이 들어가므로(`deident_key`) 그 키의 파일은 그 시도가 올린 파일이다.
    입력(마스터)이 보낸 뒤에 바뀌었으면 산출은 지금 마스터와 다른 파일에서 만들어졌다.

    시계 차이를 감안한 허용 오차는 두지 않는다. 경계에서 놓치면 새로 처리할 뿐이지만,
    허용 오차를 두면 앞선 시도의 산출을 이 시도의 산출로 잘못 볼 수 있다.
    """
    source = _parse_at(staged.get("input_modified_at"))
    output = _parse_at(staged.get("output_modified_at"))
    if source is None or output is None or source >= dispatched_at or output <= dispatched_at:
        return False
    if "coordinates_modified_at" in staged:
        faces = _parse_at(staged.get("coordinates_modified_at"))
        return faces is not None and faces > dispatched_at
    return True


async def _adopt_late_result(
    db: AsyncSession, video: Video, token: str, report: dict | None, out_bucket: str,
) -> dict | None:
    """회수한 시도의 완료 보고가 늦게 도착하면 그 결과를 새 시도로 이어받는다.

    배포로 워커가 다시 만들어지면 받아 둔 잡 메시지가 새 워커에 다시 전달된다(acks_late).
    새 워커가 회수 전의 토큰으로 처리를 끝내면 그 보고는 펜싱에서 버려지고,
    이미 끝난 처리를 처음부터 다시 돌리게 된다.

    낡은 토큰의 결과를 그대로 받지 않고, 아래 조건이 모두 맞을 때만 새 토큰으로 시도를 새로 연다.
    완료 판정은 새 토큰으로 한다.
    - 영상이 대기 중이고 토큰이 비어 있다(다른 시도가 열려 있지 않다).
    - 보고의 토큰이 회수 표식(`deident_reclaimed`)의 토큰과 같다(마지막으로 보낸 시도다).
    - 스테이징 산출과 좌표 JSON이 그 시도를 보낸 뒤에 올라왔고 입력은 그 전부터 있었다
      (`_made_after`).
    - 대기에서 실행으로 바꾸는 갱신이 경합 없이 한 행을 바꿨다.

    하나라도 맞지 않으면 `None`을 돌려주고 보고는 펜싱에서 버려진다.

    Returns:
        이어받았으면 `{token, from_token, dispatched_at}`. `token`은 새로 발급한 토큰이고
        `from_token`은 늦게 온 토큰의 앞 8자다.
    """
    marker = (video.extra or {}).get(_RECLAIMED_KEY)
    if not isinstance(marker, dict) or not token or marker.get("token") != token:
        return None
    if video.deident_status != "pending" or video.deident_token is not None:
        return None
    dispatched_at = _parse_at(marker.get("dispatched_at"))
    src = resolver.media_uri(video, "deident_input")
    if dispatched_at is None or not src:
        return None
    vid = video.video_id
    in_bucket, in_key = video_service._parse_s3_uri(src)
    coordinates = _coordinates_record(out_bucket, vid, report) is not None
    staged = _staged_evidence(out_bucket, vid, token, in_bucket, in_key,
                              coordinates=coordinates)
    if staged is None or not _made_after(staged, dispatched_at):
        log.info("deident 늦은 완료 보고를 이어받지 않음 video=%s (산출 근거 불일치)", vid)
        return None
    new_token = uuid_mod.uuid4().hex
    row = (await db.execute(
        update(Video)
        .where(Video.video_id == vid, Video.deident_status == "pending",
               Video.deident_token.is_(None))
        .values(deident_status="running", deident_token=new_token,
                deident_lease_until=datetime.now(UTC) + timedelta(seconds=_LEASE_S),
                deident_attempts=Video.deident_attempts + 1)
        .returning(Video.video_id)
    )).first()
    if row is None:
        return None
    await stage_runs.start(db, vid, "deident", detail={"from": in_key, "adopted": True})
    log.warning("deident 회수한 시도의 늦은 결과를 이어받음 video=%s req=%s… 보낸 시각=%s",
                vid, token[:8], marker.get("dispatched_at"))
    return {"token": new_token, "from_token": token[:8],
            "dispatched_at": marker.get("dispatched_at")}


async def extend_lease(
    db: AsyncSession, video_id: UUID, token: str, *, done_seconds: float | None = None,
) -> bool:
    """리스를 연장하고 처리한 미디어 시간(초)을 기록한다. 연장했으면 True를 돌려준다.

    진행률 기준은 트랜스코딩과 같은 미디어 시간이라 GPU 비식별화도 같은 진행률 화면을 쓴다.
    그 시도의 첫 하트비트이면 토큰을 `_STARTED_KEY`에 남겨 회수가 시작한 시도로 셀 수 있게 한다.
    """
    values: dict = {"deident_lease_until": datetime.now(UTC) + timedelta(seconds=_LEASE_S)}
    if done_seconds is not None:
        values["deident_done_seconds"] = float(done_seconds)
    row = (await db.execute(
        update(Video)
        .where(Video.video_id == video_id, Video.deident_token == token,
               Video.deident_status == "running")
        .values(**values)
        .returning(Video.video_id, Video.extra[_STARTED_KEY].astext)
    )).first()
    if row is not None and row[1] != token:
        await video_service.merge_extra(db, video_id, {_STARTED_KEY: token})
    await db.commit()
    return row is not None


async def complete(
    db: AsyncSession, video_id: UUID, token: str,
    *, ok: bool, error: str | None = None, transient: bool = False,
    elapsed_s: float | None = None, report: dict | None = None,
) -> bool:
    """워커의 완료 보고를 판정한다. 현재 토큰의 보고만 받아들인다(펜싱).

    성공하면 산출을 올린 시도의 스테이징 산출(`deident_key`)이 마스터를 대체한다.
    좌표 JSON을 올렸다는 보고가 있으면 좌표도 최종 키로 옮긴다.
    publish가 그 마스터를 분석본으로 확정하므로 이후 MI는 비식별 사본을 읽는다.

    낡은 토큰의 성공 보고는 버린다. 회수한 시도의 결과가 늦게 도착했고 산출이 그 시도에서
    왔다는 근거가 있을 때만 새 토큰으로 이어받는다(`_adopt_late_result`).

    Args:
        report: 워커가 보고한 모델·버전·검출 수. 아무것도 가리지 않았다는 보고도 이력에 그대로
            저장한다. `coordinates` 항목은 좌표 JSON을 올렸는지 나타내며, 올렸을 때만
            좌표 키 기록을 `coordinates`로 남긴다.
    """
    video = await db.get(Video, video_id)
    if video is None:
        return False

    # 산출 버킷은 행을 갱신하기 전에 읽어 둔다. 아래 갱신·병합 뒤에 `extra`를 읽으면
    # 만료된 속성을 동기 경로에서 다시 읽다가 실패한다.
    await storage.ensure_loaded(db, video.extra)
    out_bucket = derived_bucket(video)
    if ok:
        adopted = None
        # 산출을 올린 시도의 토큰. 늦은 결과를 이어받으면 `token`은 새 토큰으로 바뀌지만
        # 산출은 원래 시도의 스테이징 키에 있다.
        source_token = token
        if token != video.deident_token:
            # 낡은 토큰이다. 회수한 시도의 결과가 늦게 도착한 경우만 새 시도로 이어받는다.
            adopted = await _adopt_late_result(db, video, token, report, out_bucket)
            if adopted is not None:
                token = adopted.pop("token")
        row = (await db.execute(
            update(Video)
            .where(Video.video_id == video_id, Video.deident_token == token,
                   Video.deident_status == "running")
            .values(deident_status="done", deident_token=None, deident_lease_until=None)
            .returning(Video.video_id)
        )).first()
        if row:
            bucket = out_bucket
            coordinates = _coordinates_record(bucket, video_id, report)
            try:
                # 좌표를 먼저 옮긴다. 마스터를 옮긴 뒤 좌표에서 실패하면 마스킹된 마스터가
                # 다음 시도의 입력이 되어 한 번 더 마스킹된다.
                if coordinates:
                    promote_staged(bucket, coordinates_staging_key(video_id, source_token),
                                   coordinates_key(video_id))
                promote_staged(bucket, deident_key(video_id, source_token), master_key(video_id))
            except Exception as exc:  # noqa: BLE001 — 승격 실패를 성공으로 접지 않는다
                # 예외가 태스크 밖으로 나가면 롤백되고, 회수가 시도 수를 되돌려 GPU 처리를
                # 처음부터 다시 한다. 원인이 영구적이면(단일 copy의 5GB 한계 등) 끝나지 않는다.
                # boto3가 일시 오류를 이미 재시도했으므로 영구 오류로 세어 상한에서 멈춘다.
                await db.rollback()
                log.error("deident 산출 승격 실패 video=%s: %s: %s",
                          video_id, type(exc).__name__, exc)
                return await complete(
                    db, video_id, token, ok=False,
                    # 화면 사유에는 예외 타입을 넣지 않는다. 원문과 타입은 위 log.error가 남긴다.
                    # `error_text`는 한글이 섞인 사유를 운영자용 문구로 보고 그대로 내보낸다.
                    error="결과 파일을 저장소에 올리지 못했습니다",
                    transient=False)
            # `replaced`는 산출이 분석본 마스터(analysis-720p)를 대체했다는 기록이다.
            info = {"elapsed_s": elapsed_s, "replaced": "analysis-720p",
                    **(report or {}), "bucket": bucket}
            # 워커의 `coordinates` 보고를 좌표 키 기록으로 바꾼다.
            # 올리지 않았으면 없는 파일을 기록하지 않도록 항목을 뺀다.
            info.pop("coordinates", None)
            if coordinates:
                info["coordinates"] = coordinates
            if adopted is not None:
                # 회수한 시도가 만든 결과라서 어느 시도였는지 이력에 남긴다.
                info["adopted"] = adopted
            await video_service.merge_extra(
                db, video_id, {"deident": info},
                remove=(_ERROR_KEY, _RECLAIMED_KEY, _STREAK_KEY, _RETRY_AFTER_KEY, _STARTED_KEY))
            await stage_runs.finish(db, video_id, "deident", ok=True, detail=info)
            # 다음 단계는 `pipeline.open_next`가 정한다.
            from api_gateway.services.media.pipeline import open_next
            await db.refresh(video)
            await open_next(db, video)
        await db.commit()
        if not row:
            log.warning("deident 펜싱 거부 — 낡은 완료 보고 폐기 video=%s", video_id)
            # 이어받지 않은 낡은 시도의 산출과 좌표를 지운다. 앱 경로에는 버킷 만료 규칙이 없다.
            # 키에 그 시도의 토큰이 있어 현재 시도의 산출과 겹치지 않는다.
            for key in (deident_key(video_id, source_token),
                        coordinates_staging_key(video_id, source_token)):
                try:
                    storage.delete_object(bucket=out_bucket, key=key)
                except Exception as exc:  # noqa: BLE001 — 남은 객체가 판정보다 덜 중요하다
                    log.warning("deident 낡은 산출 정리 실패 video=%s key=%s: %s",
                                video_id, key, exc)
        return row is not None

    # 단계 이력에는 `[일시]` 표식을 붙이지 않는다. 일시 여부는 `detail.transient`에 싣는다
    # (`transcode.complete_transcode`와 같은 규칙).
    said = (error or "사유를 받지 못했습니다.")[:400]
    msg = f"{'[일시] ' if transient else ''}{said}"
    # 일시 오류는 시도를 소모하지 않고 연속 횟수를 센다. 상한이 없으면 저장소 장애 동안
    # 영상이 대기와 실행을 끝없이 오간다.
    streak = int((video.extra or {}).get(_STREAK_KEY) or 0) + 1 if transient else 0
    if transient and streak >= _TRANSIENT_LIMIT:
        msg = f"{said.rstrip('.')}. 일시 오류가 {streak}회 이어져 자동 재시도를 멈췄습니다."
    if (not transient and video.deident_attempts >= _MAX_RETRIES) or (
            transient and streak >= _TRANSIENT_LIMIT):
        row = (await db.execute(
            update(Video)
            .where(Video.video_id == video_id, Video.deident_token == token,
                   Video.deident_status == "running")
            .values(deident_status="failed", deident_token=None, deident_lease_until=None)
            .returning(Video.video_id)
        )).first()
        if row:
            await stage_runs.finish(db, video_id, "deident", ok=False, error=msg)
            await video_service.merge_extra(
                db, video_id, {_ERROR_KEY: msg}, remove=(_STREAK_KEY, _RETRY_AFTER_KEY))
            # 이 시도의 스테이징 산출과 좌표 JSON을 지운다. 앱 경로에는 버킷 만료 규칙이 없다.
            # 좌표 JSON은 mp4보다 먼저 올라가므로 실패한 시도가 남겼을 수 있다.
            # 정리에 실패해도 판정은 바뀌지 않는다.
            for key in (deident_key(video_id, token), coordinates_staging_key(video_id, token)):
                try:
                    storage.delete_object(bucket=out_bucket, key=key)
                except Exception as exc:  # noqa: BLE001 — 남은 객체가 판정보다 덜 중요하다
                    log.warning("deident 산출 정리 실패 video=%s key=%s: %s",
                                video_id, key, exc)
            # 비식별화를 켜 놓고 처리하지 못했으므로 publish를 열지 않고 파이프라인을 멈춘다.
            log.error("deident 확정 실패(%s %d회) — 파이프라인 정지 video=%s: %s",
                      "일시 오류 연속" if transient else "시도", streak if transient
                      else video.deident_attempts, video_id, msg)
        await db.commit()
        return False

    row = (await db.execute(
        update(Video)
        .where(Video.video_id == video_id, Video.deident_token == token,
               Video.deident_status == "running")
        .values(
            deident_status="pending", deident_token=None, deident_lease_until=None,
            deident_attempts=Video.deident_attempts - (1 if transient else 0),
        )
        .returning(Video.video_id)
    )).first()
    if row:
        if transient:
            # 저장소 장애가 이어지는 동안 연속 횟수에 따라 간격을 늘린다(지수 백오프).
            delay = min(_BACKOFF_BASE_S * 2 ** (streak - 1), _BACKOFF_MAX_S)
            retry_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
            await stage_runs.finish(
                db, video_id, "deident", ok=False, error=said,
                detail={"transient": True, "streak": streak, "retry_after": retry_at})
            await video_service.merge_extra(db, video_id, {
                _ERROR_KEY: msg, _STREAK_KEY: streak, _RETRY_AFTER_KEY: retry_at})
        else:
            # 영구 오류가 나면 일시 오류의 연속 횟수를 초기화한다(썸네일 재시도와 같은 규칙).
            await stage_runs.finish(
                db, video_id, "deident", ok=False, error=said, detail={"transient": False})
            await video_service.merge_extra(
                db, video_id, {_ERROR_KEY: msg}, remove=(_STREAK_KEY, _RETRY_AFTER_KEY))
    await db.commit()
    return False


async def _waiting_upstream(db: AsyncSession, video: Video) -> bool:
    """비식별화를 켠 프로젝트의 `skipped` 영상이 앞 단계를 기다리는 중이면 True를 돌려준다.

    준비 타임라인과 같은 `deident_gap`으로 판정해, 타임라인이 재시도 버튼을 두지 않는 영상을
    서버도 받지 않게 한다.
    """
    downstream = bool(
        await db.scalar(select(AnalysisJob.id).where(
            AnalysisJob.video_id == video.video_id).limit(1))
        or await db.scalar(select(Clip.clip_id).where(Clip.video_id == video.video_id).limit(1))
    )
    reached = reached_deident(
        probe_status=video.probe_status, transcode_status=video.transcode_status,
        publish_status=video.publish_status, downstream=downstream,
    )
    return deident_gap(video.deident_status, True, reached=reached) == "waiting"


async def retry(db: AsyncSession, project_id: UUID, video_id: UUID, user: User) -> None:
    """비식별화를 다시 큐에 넣는다. project_admin이 부른다.

    비식별화를 켠 프로젝트의 실패 영상과 마스킹하지 않은 영상(`skipped`)을 받는다.
    비식별화를 끈 프로젝트에서는 둘 다 받지 않는다.
    그 프로젝트의 실패 영상은 `skip_failed`로 넘긴다.

    `skipped`: 비식별화를 켜기 전에 올라온 영상은 마스킹본이 없어 분석 게이트가 외부 분석으로
    보내지 않으므로 다시 마스킹할 경로가 필요하다. 마스터가 대체되지 않은 원본 프레임이라
    다시 마스킹해도 안전하다. 끈 프로젝트의 `skipped`는 정상 상태라서 받지 않는다.
    `publish`가 이미 끝났으면 재생본이 마스킹 전 마스터로 만들어졌으므로 `publish_status`를
    되돌려 재생본도 다시 만든다. 되돌리지 않으면 사람이 보는 480p 재생본에 얼굴이 남는다.

    원본이 없으면 거절한다. `skipped` 영상은 마스터가 없으면 원본을 입력으로 쓰는데,
    보관 해제된 원본을 큐에 넣으면 다운로드 단계에서 "비식별화 실패"로 끝나 원인을 잘못 찾게 된다.

    `done`은 받지 않는다. 마스킹본이 이미 마스터를 대체했으므로(`replaced: analysis-720p`)
    다시 돌리면 마스킹한 영상을 또 마스킹해 검출 수가 0에 가깝게 기록된다. 원본은 되돌릴 수 없다.

    처리 중(`queued`·`running`)이거나 자동 재시도를 기다리는(`pending`) 영상도 받지 않는다.
    멈춘 잡은 `reclaim_expired`가 되돌리고 일시 오류는 간격을 두고 다시 보내므로,
    하나 더 넣으면 같은 영상에 잡이 두 개 돈다. 이 경우는 `deident_in_progress`로 따로 거절한다.

    시도 횟수(`deident_attempts`)를 0으로 되돌린다. 되돌리지 않으면 다음 실패 한 번에 곧바로
    다시 `failed`가 된다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            처리 중이거나 자동 재시도를 기다리는 경우(409 `deident_in_progress`),
            규격 검증이나 변환이 끝나지 않아 비식별화 단계가 아직 열리지 않은 경우
            (409 `deident_waiting`),
            완료된 경우·비식별화를 쓰지 않는 프로젝트의 미마스킹이나 실패인 경우
            (409 `deident_not_retryable`),
            다시 읽을 원본이 없는 경우(409 `deident_source_gone`).
    """
    video = await db.get(Video, video_id)
    if not video or video.project_id != project_id or video.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_project_admin(db, project_id, user)
    was_skipped = video.deident_status == "skipped"
    enabled = await db.scalar(
        select(Project.deident_enabled).where(Project.project_id == project_id)
    )
    if was_skipped or video.deident_status == "failed":
        if not enabled:
            # 비식별화를 끈 프로젝트의 `skipped`는 정상 상태다. 실패 영상도 대기에 넣으면
            # `_ready`가 보내지 않고 `skip_disabled`가 건너뜀으로 넘긴다.
            raise HTTPException(status.HTTP_409_CONFLICT, "deident_not_retryable")
        if was_skipped and await _waiting_upstream(db, video):
            # 규격 검증 전이거나 변환 중인 영상이다. 비식별화는 앞 단계가 끝나면 파이프라인이 연다.
            # 지금 대기에 넣으면 검증하지 않은 원본을 변환과 동시에 비식별화한다.
            raise HTTPException(status.HTTP_409_CONFLICT, "deident_waiting")
    elif video.deident_status in ("pending", "queued", "running"):
        # 처리 중이거나 자동 재시도를 기다리는 영상이다.
        # `deident_not_retryable`로 거절하면 화면이 "완료된 영상" 사유를 잘못 표시한다.
        raise HTTPException(status.HTTP_409_CONFLICT, "deident_in_progress")
    elif video.deident_status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "deident_not_retryable")
    if resolver.media_uri(video, "deident_input") is None:
        # 대기에 넣어도 다운로드 단계에서 실패한다. 화면에는 "비식별화 실패"로 표시된다.
        raise HTTPException(status.HTTP_409_CONFLICT, "deident_source_gone")

    before = ((video.extra or {}).get(_ERROR_KEY) or "")[:200]
    video.deident_status = "pending"
    video.deident_attempts = 0
    video.deident_token = None
    video.deident_lease_until = None
    if was_skipped and video.publish_status == "done":
        # 기존 재생본은 마스킹 전의 마스터로 만들었으므로 상태를 되돌려 publish를 다시 연다.
        video.publish_status = "skipped"
    await db.flush()
    # 실패 기록, 일시 오류 연속 횟수, 재시도 시각, 회수 표식을 지운다. 남기면 화면이 계속
    # 실패로 표시하고, 앞선 연속 횟수나 재시도 시각 때문에 곧바로 멈추거나 투입되지 않는다.
    await video_service.merge_extra(
        db, video_id, {}, remove=(_ERROR_KEY, _STREAK_KEY, _RETRY_AFTER_KEY, _RECLAIMED_KEY))
    audit.emit(
        db, event_type="video.deident_retried", target_kind="video", target_id=video_id,
        actor_user_id=user.user_id, project_id=project_id,
        # 실패 복구와 마스킹하지 않은 영상의 뒤늦은 마스킹을 구분해 남긴다.
        payload={"previous_error": before, "from": "skipped" if was_skipped else "failed"},
    )
    await db.commit()


async def reclaim_expired(db: AsyncSession, *, revoke: Revoke | None = None) -> int:
    """리스가 만료된 잡을 회수한다. 워커가 비정상 종료돼도 처리가 지연될 뿐 멈추지 않는다.

    시작하지 못한 시도(배포로 워커가 다시 만들어진 경우 등)는 시도 수를 소모하지 않는다.
    시작 하트비트가 온 뒤 회수된 시도는 시도로 세고, 상한(`_MAX_RETRIES`)에 닿으면 `failed`로
    확정해 파이프라인을 멈춘다. 워커 자식 프로세스를 죽이는 입력(GPU 메모리 초과 등)은
    실패 보고 없이 끝나므로 여기서 세지 않으면 끝없이 다시 돈다.
    시작 여부는 하트비트가 남긴 토큰(`_STARTED_KEY`)으로 가른다.

    회수한 시도의 단계 이력도 일시 오류로 닫는다. 닫지 않으면 다음 시도가 시작될 때까지
    타임라인이 워커 없는 시도를 "진행 중"으로 표시한다(`transcode.reclaim_expired`와 같은 규칙).

    회수 표식(`deident_reclaimed`)에 지운 토큰과 그 시도의 시작 시각을 남긴다.
    다음 투입은 이 영상을 먼저 보낸다(`_reclaimed_first`).
    옛 토큰은 커밋한 뒤 `revoke`로 넘겨 큐에 남은 옛 메시지를 워커가 시작 전에 버리게 한다.
    이미 실행 중인 옛 시도는 취소하지 않고 그 결과를 회수 표식으로 이어받는다.
    """
    now = datetime.now(UTC)
    # 지울 토큰을 먼저 읽는다. UPDATE의 RETURNING은 바뀐 값(빈 토큰)을 돌려준다.
    expired = (await db.execute(
        select(Video.video_id, Video.deident_token, Video.extra[_STARTED_KEY].astext,
               Video.deident_attempts)
        .where(Video.deident_status == "running", Video.deident_lease_until < now)
        .with_for_update(skip_locked=True)
    )).all()
    capped: list = []
    for vid, token, started_token, attempts in expired:
        dispatched_at = await stage_runs.open_started_at(db, vid, "deident")
        started = token is not None and started_token == token
        if started and attempts >= _MAX_RETRIES:
            note = crash_cap_note(attempts)
            await db.execute(
                update(Video).where(Video.video_id == vid)
                .values(deident_status="failed", deident_token=None, deident_lease_until=None))
            await stage_runs.finish(db, vid, "deident", ok=False, error=note,
                                    detail={"transient": False, "reclaimed": True})
            await video_service.merge_extra(
                db, vid, {_ERROR_KEY: note},
                remove=(_STREAK_KEY, _RETRY_AFTER_KEY, _RECLAIMED_KEY, _STARTED_KEY))
            capped.append((vid, token))
            log.error("deident 확정 실패(시작 뒤 회수 %d회) — 파이프라인 정지 video=%s",
                      attempts, vid)
            continue
        await db.execute(
            update(Video)
            .where(Video.video_id == vid)
            .values(deident_status="pending", deident_token=None, deident_lease_until=None,
                    deident_attempts=Video.deident_attempts - (0 if started else 1))
        )
        await stage_runs.finish(db, vid, "deident", ok=False, error=_RECLAIM_NOTE,
                                detail={"transient": True, "reclaimed": True})
        # 회수 표식은 다음 투입의 우선순위(`_pick`)와
        # 늦은 완료 보고의 확인(`_adopt_late_result`)에 쓴다.
        marker = {"token": token, "reclaimed_at": now.isoformat()}
        if dispatched_at is not None:
            marker["dispatched_at"] = dispatched_at.isoformat()
        await video_service.merge_extra(db, vid, {_RECLAIMED_KEY: marker},
                                        remove=(_STARTED_KEY,))
    await db.commit()
    # 실패로 확정한 시도의 스테이징 산출을 지운다. 정리에 실패해도 판정은 바뀌지 않는다.
    for vid, token in capped:
        if not token:
            continue
        video = await db.get(Video, vid)
        await storage.ensure_loaded(db, video.extra if video else None)
        bucket = derived_bucket(video) if video else None
        for key in (deident_key(vid, token), coordinates_staging_key(vid, token)):
            try:
                storage.delete_object(bucket=bucket, key=key)
            except Exception as exc:  # noqa: BLE001 — 남은 객체가 판정보다 덜 중요하다
                log.warning("deident 산출 정리 실패 video=%s key=%s: %s", vid, key, exc)
    _revoke(revoke, [t for _, t, _, _ in expired if t])
    if expired:
        log.warning("deident 리스 만료 회수 %d건", len(expired))
    return len(expired)
