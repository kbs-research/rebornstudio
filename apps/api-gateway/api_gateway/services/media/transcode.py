"""트랜스코딩 판정(`needs_transcode`)과 잡 프로토콜의 DB 쪽 처리.

`build_jobs`(30초 주기 beat)가 `pending` 영상을 상한만큼 골라 잡 페이로드를 만들고
`q.transcode`에 넣는다.
페이로드에는 입력 presigned GET, 타깃 사양, 산출 presigned PUT, 펜싱 토큰이 들어 있다.
워커는 DB에 접근하지 않고 `reborn_transcode.run_job`이 페이로드만으로 인코딩·업로드한다.
하트비트(`extend_lease`)와 완료(`complete_transcode`)는 큐 메시지로 돌아온다.

안전장치는 펜싱(자기 토큰일 때만 반영), 리스와 회수,
일시 오류의 시도 환불, 영구 오류의 재시도 상한이다.
"""

from __future__ import annotations

import logging
import uuid as uuid_mod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from reborn_transcode import output_check
from sqlalchemy import DateTime, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import User, Video
from api_gateway.services import audit, authz, error_text
from api_gateway.services.joblock import lock_for_transaction
from api_gateway.services.media import resolver, stage_runs, storage
from api_gateway.services.media import video as video_service

log = logging.getLogger(__name__)

# 실패 사유를 담는 `extra` 키
_ERROR_KEY = "transcode_error"
_MAX_RETRIES = 3  # 영구 오류 재시도 상한. 닿으면 `failed`로 확정한다



def derived_bucket(video: Video) -> str:
    """변환 산출이 들어갈 버킷 이름을 돌려준다.

    영상 행을 만들 때 기록한 프로젝트 버킷(`extra.storage`, `storage.bucket_of`)이다.
    프로젝트 저장소는 첫 영상을 등록한 뒤에는 바꿀 수 없으므로
    한 프로젝트의 산출은 한 버킷에 모인다.
    MI는 자산 등록 때 `bucketName`을 명시해 받으므로 워크스페이스에 연동하지 않은 버킷도 읽는다.

    주의: 원본이 있던 버킷을 따르지 않는다.
    원본 버킷을 따르면 산출이 등록 경로에 따라 여러 버킷에 흩어져 라이프사이클 규칙, 비용 집계,
    정리 기준을 버킷 하나로 세울 수 없다. 원본이 클라우드에 없는 로컬 반입에도 쓸 수 없다.
    """
    return storage.bucket_of(video.extra)


def master_bucket(video: Video) -> str:
    """분석본 마스터가 있는 버킷 이름을 돌려준다. 산출과 함께 기록한 버킷을 먼저 읽는다.

    트랜스코딩은 `extra.transcode.bucket`에, 비식별화는 `extra.deident.bucket`에 기록한다.
    산출과 함께 기록한 버킷이 마스터가 실제로 있는 곳이다. 둘 다 없으면 `derived_bucket`을 쓴다.
    """
    extra = video.extra or {}
    for step in ("transcode", "deident"):
        recorded = (extra.get(step) or {}).get("bucket")
        if recorded:
            return str(recorded)
    return derived_bucket(video)


# ── 판정: 이 한 곳에서 한다 ─────────────────────────────────────────────────

def _spec_reasons(meta: dict) -> list[str]:
    """원본이 MI 규격을 벗어난 사유 목록을 돌려준다. 이 사유가 분석본을 만드는 근거다.

    조건은 `codec != 'h264' OR height > 720 OR 컨테이너 미지원`이다. 출력 코덱이 h264로 고정이므로
    기준도 h264다. 이미 720p여도 코덱(av1 등)이 다르면 규격 위반이라 해상도와 코덱을 함께 본다.

    값이 None이면(probe가 측정하지 못한 값) 사유로 세지 않는다(probe `check_spec`과 같은 규칙).
    측정 실패를 규격 위반으로 세면 probe 도구 문제로 불필요한 인코딩이 돈다.
    """
    reasons = []
    codec = (meta.get("video_codec") or "").lower()
    height = meta.get("height")
    if codec and codec != "h264":
        reasons.append(f"codec:{codec}")
    if height and height > _ANALYSIS_HEIGHT:
        reasons.append(f"height:{height}")
    # 컨테이너 검사. 산출은 mp4로 고정이라 MI가 받지 않는 컨테이너도 변환으로 고친다.
    # 이 검사가 없으면 h264 · 720p이고 컨테이너만 맞지 않는 영상이 변환되지 않은 채 거부된다.
    fmt = {f.strip().lower() for f in (meta.get("format_name") or "").split(",") if f.strip()}
    if fmt and not (fmt & _MI_CONTAINERS):
        reasons.append(f"container:{meta.get('format_name')}")
    return reasons


def plan_outputs(meta: dict, profile: str = "auto") -> list[str]:
    """영상에 필요한 산출을 정한다. 산출 결정은 이 함수에서만 한다.

    두 산출은 목적이 다르므로 필요 조건도 다르다.

    - 분석본(720p): MI가 읽는다. 원본이 MI 규격을 벗어날 때만 필요하다.
    - 재생본(480p): 사람이 본다. 480p를 넘거나 브라우저가 재생하지 못하는 코덱·컨테이너일 때
      필요하다.

    분석본을 만들지 않으면 `resolver`가 원본을 그대로 MI에 넘긴다(`analysis_input`의 폴백).
    하나라도 필요할 때 둘 다 만들면 `h264 · 720p · mp4` 원본도 재생본 때문에 원본과 같은 분석본을
    다시 인코딩한다. 분석본(720p CRF 24)은 재생본(480p CRF 26)보다 인코딩 비용이 크다.

    Args:
        meta: probe 결과(`extra["probe"]`).
        profile: 변환 정책. auto(판정에 따름), always(항상 둘 다), never(산출 없음).

    Returns:
        list[str]: 필요한 산출 이름("analysis"·"playback") 목록. 빈 목록이면 변환하지 않는다.
    """
    if profile == "never":
        return []
    if profile == "always":
        return ["analysis", "playback"]
    spec = _spec_reasons(meta)
    out = ["analysis"] if spec else []
    height = meta.get("height")
    # 규격 위반(코덱·컨테이너)은 재생에도 문제가 된다.
    # 브라우저가 av1·mkv를 재생하지 못하면 라벨러 화면에 영상이 나오지 않는다.
    # 그래서 규격 위반이면 해상도와 관계없이 재생본도 만든다.
    if spec or (height and height > _PLAYBACK_HEIGHT):
        out.append("playback")
    return out


def needs_transcode(meta: dict, profile: str = "auto") -> str | None:
    """재인코딩이 필요한지 판정해 사람이 읽는 사유 문자열로 돌려준다.

    `plan_outputs`와 결과가 어긋나지 않도록 같은 근거(`_spec_reasons`)에서 계산한다.

    Returns:
        str | None: 변환 대상이면 사유(화면·감사 기록에 그대로 실린다), 아니면 None.
    """
    if profile == "never":
        return None
    if profile == "always":
        return "profile:always"
    reasons = _spec_reasons(meta)
    height = meta.get("height")
    # 재생본(480p)만 필요한 경우. 이 분기가 없으면 `h264 · 720p` 영상의
    # 480p 재생본이 만들어지지 않아 라벨러가 원본(수 GB)을 그대로 스트리밍한다.
    if height and height > _PLAYBACK_HEIGHT and not reasons:
        reasons.append(f"playback:{height}")
    return ",".join(reasons) or None


# MI가 받는 컨테이너 목록. probe의 허용 목록과 같은 값을 변환 필요 여부 판정에 쓴다.
# 규격 판정은 probe(`check_spec`)가 하고, 이 모듈은 규격 위반을 변환으로 고칠 수 있는지만 본다.
_MI_CONTAINERS = {"mp4", "mov", "matroska", "webm"}


# ── 스케일 아웃 실행 골격 ──────────────────────────────────────────────────

# 리스 TTL(초). 연장 주기(ffmpeg poll, 수 초)보다 넉넉하게 둔다.
# 만료되면 워커가 비정상 종료된 것으로 본다.
_LEASE_S = 300
# 투입 때 거는 리스(초). 워커가 잡을 가져가기 전까지 큐에서 기다리는 시간을 덮는다.
# 워커의 시작 하트비트가 리스를 `_LEASE_S`로 줄인다.
# 투입 때부터 `_LEASE_S`를 걸면 앞선 긴 변환이 워커를 점유한 동안 대기 중인 잡이 회수되고,
# 다시 투입된 잡과 큐에 남은 옛 메시지가 같은 영상을 두 번 변환한다.
_QUEUE_WAIT_S = 4 * 3600
# 시작한 잡 하나의 처리 시간 상한(초). 입력 길이 상한에 가장 느린 처리 배율을 곱한다.
# 가장 느린 잡은 GPU 없이 도는 비식별화(검출 두 번과 가림 인코딩)이고
# 실시간의 1/3보다 빠르다고 본다.
#   처리 상한 = 입력 길이 상한(probe_max_duration_seconds, 6시간) × 3 = 18시간
_SLOWEST_REALTIME_FACTOR = 3
_MAX_PROCESSING_S = int(settings.probe_max_duration_seconds * _SLOWEST_REALTIME_FACTOR)
# 잡 URL(입력 GET·산출 PUT)의 유효 기간(초). 큐 대기 상한과 처리 상한의 합이다(기본 22시간).
# 서명을 투입 때 하므로 유효 기간에 큐 대기가 들어간다. 큐 대기만 덮으면 늦게 시작한 잡의
# 마지막 업로드가 403으로 실패하고, 일시 오류로 분류되어 같은 잡이 처음부터 다시 돈다.
# 낡은 시도가 유효 기간 안에 올려도 그 시도의 스테이징 키에만 쓴다(`staging_key`).
# 브로커 재전달 시간(`reborn_contracts.queues.BROKER_VISIBILITY_TIMEOUT_S`)은 이 값보다 길어야 한다.
_JOB_URL_TTL_S = _QUEUE_WAIT_S + _MAX_PROCESSING_S
# 시작 하트비트가 한 번도 오지 않은 시도를 회수할 때 남기는 사유. 화면의 시도 줄에 그대로 표시된다.
# 처리 서버가 잡을 가져가지 못한 경우라서 응답이 끊긴 경우와 구분한다.
_NOT_STARTED_NOTE = "처리 서버가 작업을 시작하지 못했습니다."


def crash_cap_note(attempts: int) -> str:
    """시작한 뒤 회수된 시도가 상한에 닿았을 때 남기는 사유를 만든다. 화면에 그대로 표시된다."""
    return f"처리 중에 처리 서버가 {attempts}번 멈춰 자동 재시도를 멈췄습니다."

#: 회수한 시도의 옛 토큰을 받아 큐 메시지를 취소하는 함수. 잡 메시지의 id가 토큰이다.
#: 서비스는 큐에 접근하지 않으므로 회수를 부르는 쪽(worker-io)이 넘긴다.
Revoke = Callable[[list[str]], None]
_ANALYSIS_HEIGHT, _ANALYSIS_CRF = 720, 24   # MAIU 입력. 사람이 보지 않는다
_PLAYBACK_HEIGHT, _PLAYBACK_CRF = 480, 26   # 라벨링·검수 재생용. 대역폭 −59%

# 산출별 x264 프리셋. 두 산출은 쓰는 곳이 달라 프리셋을 나눈다.
# 재생본은 사람이 보는 프록시라 veryfast를 쓴다.
# fast보다 1.7배 빠르고 23% 작으며 SSIM은 0.989에서 0.985다.
# 분석본은 MI가 읽고 비식별화의 입력도 되므로 fast를 쓴다.
# 화질이 장면 분할·요약·얼굴 검출에 미치는 영향을 측정하기 전에는 바꾸지 않는다.
_ANALYSIS_PRESET = "fast"
_PLAYBACK_PRESET = "veryfast"
_EXTEND_EVERY_S = 60   # 하트비트 주기(초). `_LEASE_S`의 절반보다 짧아야 한다
# `queued` 상태로 이 시간(초)이 지나면 큐 메시지가 유실된 것으로 본다.
# 브로커 유실처럼 acks_late가 막지 못하는 장애에 대비한다.
_QUEUED_STALE_S = 600
#: 연속 일시 오류의 상한. 연속 횟수가 이 값에 이르면 실패로 확정한다.
#: 상한이 없으면 만료된 URL(403)처럼 다시 돌아도 낫지 않는 일시 오류가 끝없이 반복된다.
#: 재시도 간격은 `_BACKOFF_BASE_S`부터 두 배씩 늘고 `_BACKOFF_MAX_S`에서 더 늘지 않는다.
#: 확정까지 기다리는 시간의 합은 비식별화(`deident._TRANSIENT_LIMIT`)와 같은 약 2.5시간이다.
_TRANSIENT_LIMIT = 10
_BACKOFF_BASE_S = 60
_BACKOFF_MAX_S = 1800
#: 연속 일시 오류 횟수의 `extra` 키. 성공하거나 영구 오류가 나면 지운다.
_STREAK_KEY = "transcode_transient_streak"
#: 다음 시도를 보낼 수 있는 시각(ISO)의 `extra` 키. 이 시각 전에는 투입하지 않는다.
_RETRY_AFTER_KEY = "transcode_retry_after"


async def build_jobs(db: AsyncSession) -> list[dict]:
    """`pending` 영상을 상한만큼 골라 워커용 잡 페이로드로 만든다.

    상태 전이(`pending`에서 `running`으로), 토큰·리스, 입력 presigned GET,
    타깃 사양(업스케일 금지 계산), 산출 presigned PUT을 여기서 모두 준비한다.

    리스는 두 단계로 건다. 투입 때는 큐 대기를 덮는 `_QUEUE_WAIT_S`를 걸고,
    워커의 시작 하트비트가 `_LEASE_S`로 줄인다.
    상한은 단계마다 따로 세는데 재생본·장면 분할이 같은 큐를 쓰므로 큐에는 워커 수보다 많은 잡이
    쌓일 수 있다. 시작한 뒤 워커가 비정상 종료되면 `reclaim_expired`가 `_LEASE_S` 안에 되살린다.

    빌더가 동시에 돌 수 있으므로 상한을 세기 전에 임계 구역을 잠근다(`lock_for_transaction`).

    Returns:
        list[dict]: `queues.md`의 `transcode_one` 잡 스키마.
    """
    await lock_for_transaction(db, "reborn:dispatch:transcode")
    limit = settings.transcode_max_inflight - (
        await db.scalar(
            select(func.count()).select_from(Video)
            .where(Video.transcode_status.in_(("queued", "running")))
        ) or 0
    )
    if limit <= 0:
        return []
    retry_after = Video.extra[_RETRY_AFTER_KEY].astext
    videos = list(
        (await db.scalars(
            select(Video)
            .where(Video.transcode_status == "pending", Video.deleted_at.is_(None),
                   # 일시 오류 뒤 재시도를 기다리는 영상은 재시도 시각이 지나야 보낸다.
                   or_(retry_after.is_(None),
                       cast(retry_after, DateTime(timezone=True)) <= datetime.now(UTC)))
            .order_by(Video.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )).all()
    )
    jobs: list[dict] = []
    now = datetime.now(UTC)
    for video in videos:
        # 이 영상의 저장소 연결·버킷이 이 프로세스에 없으면 서명 전에 적재한다.
        await storage.ensure_loaded(db, video.extra)
        # 입력은 매체 결정자(`resolver`)가 고른다. `transcode_input`은 원본이다.
        src = resolver.media_uri(video, "transcode_input")
        if not src:
            video.transcode_status = "failed"
            # 화면(준비 진행 사유)에 그대로 표시되는 문장이라 `URI` 대신 `파일 위치`라고 쓴다.
            await stage_runs.finish(
                db, video.video_id, "transcode", ok=False, error="원본 파일 위치가 없습니다.")
            await video_service.merge_extra(
                db, video.video_id, {_ERROR_KEY: "원본 파일 위치가 없습니다."})
            continue
        in_bucket, in_key = video_service._parse_s3_uri(src)
        out_bucket = derived_bucket(video)
        meta = (video.extra or {}).get("probe") or {}
        src_h = meta.get("height")
        labels = plan_outputs(meta, video.transcode_profile)
        if "analysis" not in labels:
            # 큐에 들어온 뒤 재검사로 probe가 바뀌면 분석본이 필요 없어질 수 있다.
            # 그때는 잡을 내보내지 않고 `skipped`로 끝낸다. 끝내지 않으면 주기마다 잡을 다시 만든다.
            # 재생본이 필요하면 publish가 원본을 마스터로 삼아 만든다.
            video.transcode_status = "skipped"
            from api_gateway.services.media.pipeline import open_next
            await open_next(db, video)
            log.info("transcode 불필요 — 마스터가 필요 없음 video=%s", video.video_id)
            continue
        token = uuid_mod.uuid4().hex
        video.transcode_status = "running"
        # 진행 중에도 무엇을 인코딩하는지 표시할 수 있게 산출 계획을 시작 시점에 남긴다.
        await stage_runs.start(db, video.video_id, "transcode", detail={"outputs": labels})
        video.transcode_token = token
        video.transcode_lease_until = now + timedelta(seconds=_QUEUE_WAIT_S)
        video.transcode_attempts = video.transcode_attempts + 1
        # 진행 초를 비운다. 시작 하트비트가 0으로 채우므로 회수 때 시작 여부를 가르는 근거가 된다.
        # 비우지 않으면 새 시도가 시작하기 전부터 지난 시도의 진행률이 표시된다.
        video.transcode_done_seconds = None
        jobs.append({
            "video_id": str(video.video_id),
            "token": token,
            # URL이 만료(403)되면 워커가 일시 오류로 분류하고
            # 다음 `build_jobs`가 새 URL을 발급한다.
            "input_url": storage.presign_get_for(bucket=in_bucket, key=in_key,
                                                 ttl_seconds=_JOB_URL_TTL_S),
            # 분석본 마스터 하나만 만든다. 마스터와 재생본 사이에 비식별화가 들어가므로
            # 재생본은 publish가 마스터에서 만든다.
            # 산출은 이 시도의 스테이징 키(`staging_key`)에 두고 완료 판정이 마스터 키로 옮긴다.
            # 최종 분석본 위치는 MI가 곧바로 읽으므로 비식별화 전 파일을 두지 않는다.
            "targets": [{
                "label": "analysis-720p",
                "height": (_ANALYSIS_HEIGHT
                           if src_h and src_h > _ANALYSIS_HEIGHT else None),
                "crf": _ANALYSIS_CRF,
                "preset": _ANALYSIS_PRESET,
                "threads": settings.transcode_threads,
                "put_url": storage.presign_put_for(
                    bucket=out_bucket, key=staging_key(video.video_id, token, _MASTER_FILE),
                    ttl_seconds=_JOB_URL_TTL_S),
                "content_type": "video/mp4",
            }],
            "heartbeat_every_s": _EXTEND_EVERY_S,
        })
    await db.commit()
    return jobs


def _revoke(revoke: Revoke | None, tokens: list[str]) -> None:
    """회수한 시도의 큐 메시지 취소를 `revoke`에 넘긴다.

    변환·재생본·비식별화·장면 분할 회수가 함께 쓴다.

    취소는 최선 노력이다. 취소하지 못해도 그 시도의 완료 보고는 펜싱이 버리므로
    결과가 섞이지 않는다.
    """
    if revoke is None or not tokens:
        return
    try:
        revoke(tokens)
    except Exception:  # noqa: BLE001 — 취소 실패가 회수를 되돌리지 않는다
        log.warning("회수한 시도의 큐 메시지 취소 실패 %d건", len(tokens), exc_info=True)


def analysis_key(video_id: UUID) -> str:
    """분석본의 최종 키를 만든다. 키가 영상 ID로 정해지므로 완료 보고로 키를 돌려받지 않는다."""
    return f"transcoded/720p/{video_id}.mp4"


def playback_key(video_id: UUID) -> str:
    """재생본 키를 만든다. source-url은 이 키를 우선해서 돌려준다."""
    return f"transcoded/480p/{video_id}.mp4"


def master_key(video_id: UUID) -> str:
    """분석본 마스터의 스테이징 키를 만든다.

    워커는 이 키에 직접 쓰지 않는다. 변환과 비식별화의 완료 판정이 자기 시도의 스테이징 키
    (`staging_key`)에서 이 키로 옮긴다.
    deident는 이 키에서 읽고, publish는 이 키에서 최종 키로 옮긴다.
    최종 분석본 위치는 MI가 곧바로 읽으므로 비식별화 전 파일을 두지 않으려고
    스테이징 키를 따로 둔다.
    """
    return f"work/{video_id}/analysis-720p.mp4"


#: 변환 시도가 올리는 마스터 파일 이름. 스테이징 키의 마지막 부분이다.
_MASTER_FILE = "analysis-720p.mp4"


def staging_key(video_id: UUID, token: str, name: str) -> str:
    """시도 하나가 산출을 올리는 스테이징 키를 만든다. 키에 그 시도의 펜싱 토큰이 들어간다.

    잡 URL은 시도가 끝난 뒤에도 유효 기간 동안 쓸 수 있다. 회수된 시도의 워커가 늦게 올리면
    완료 보고는 펜싱이 버리지만 업로드는 막지 못한다. 시도마다 키가 다르면 늦은 업로드가
    현재 시도의 파일이나 마스터를 덮어쓰지 못한다. 완료 판정은 자기 토큰의 키만 옮긴다.
    """
    return f"work/{video_id}/{token}/{name}"


def promote_staged(bucket: str, src_key: str, dst_key: str) -> None:
    """스테이징 산출을 정해진 키로 서버 측 복사하고 스테이징 파일을 지운다.

    복사가 실패하면 예외를 그대로 낸다. 부르는 쪽이 실패로 기록한다.
    지우기만 실패하면 경고만 남긴다. 결과 파일은 이미 정해진 키에 있다.
    """
    storage.copy_object(bucket=bucket, src_key=src_key, dst_key=dst_key)
    try:
        storage.delete_object(bucket=bucket, key=src_key)
    except Exception:  # noqa: BLE001 — 남은 스테이징 파일이 판정보다 덜 중요하다
        log.warning("스테이징 산출 정리 실패 key=%s", src_key)


async def extend_lease(
    db: AsyncSession, video_id: UUID, token: str, *, done_seconds: float | None = None,
) -> bool:
    """리스를 연장하고 진행 초를 기록한다. 워커의 하트비트 메시지가 부른다.

    진행 초는 워커가 ffmpeg `-progress`에서 읽은 인코딩한 미디어 시간이다.
    벽시계 시간과 달리 하드웨어(CPU·GPU)가 바뀌어도 같은 기준으로 남은 작업량을 비교할 수 있다.
    진행률은 `done_seconds / duration_seconds`이고 분모는 probe가 기록한 값이다.
    분모가 없으면 화면은 진행률을 표시하지 않는다.

    토큰이 다르면 아무것도 쓰지 않는다(펜싱).
    """
    values: dict = {"transcode_lease_until": datetime.now(UTC) + timedelta(seconds=_LEASE_S)}
    if done_seconds is not None:
        values["transcode_done_seconds"] = float(done_seconds)
    row = (await db.execute(
        update(Video)
        .where(
            Video.video_id == video_id,
            Video.transcode_token == token,
            Video.transcode_status == "running",
        )
        .values(**values)
        .returning(Video.video_id)
    )).first()
    await db.commit()
    return row is not None


# ── 산출 검사: 판정은 이 한 곳에서 한다 ───────────────────────────────────────

#: 워커가 보낸 측정값 중 저장하는 필드. `job_runner._FACTS`와 같은 형식이다(queues.md).
_MEASURED = ("duration_seconds", "fps", "video_codec", "width", "height", "size_bytes")


@dataclass(frozen=True)
class OutputVerdict:
    """산출 검사 한 번의 판정 결과. 변환(분석본)과 재생본 확정이 같은 형식을 쓴다."""

    #: 이 단계 산출의 측정값과 원본 대비 결과 한 행. `extra`와 단계 이력에 그대로 남긴다.
    #: 워커가 측정값을 보내지 않았으면 None이다.
    measured: dict | None
    #: 위반 코드(`reborn_transcode.output_check`의 상수). 통과면 None이다.
    cause: str | None
    #: 화면에 보일 사유(운영자용 문구). 위반일 때만 있다.
    error: str | None
    #: 위반이 일시 오류이면 True. 시도를 소모하지 않고 다시 돈다(`output_check.TRANSIENT_CAUSES`).
    transient: bool = False


def judge_outputs(src_meta: dict, outputs: list | None, *, name: str) -> OutputVerdict:
    """워커가 보낸 산출 측정값을 원본 probe와 비교한다. 판정은 이 함수에서만 한다.

    `complete_transcode`(분석본)와 `publish.complete`(재생본)가 부른다.
    비교 규칙은 `reborn_transcode.output_check.check_output`에 있다. 이 함수는 비교 결과를
    저장할 한 행과 화면 사유(`error_text.output_check_text`)로 바꾼다.

    Args:
        src_meta: 원본 probe(`extra["probe"]`). 산출을 무엇에서 만들었든 원본과 비교한다.
        outputs: 워커 회신의 산출 측정값 목록. None이나 빈 목록이면 검사하지 않는다.
        name: 사유 문장에 들어갈 산출 이름(`분석본`·`재생본`).

    Returns:
        OutputVerdict: 위반이 있으면 첫 위반을, 없으면 첫 산출을 `measured`로 싣는다.
    """
    rows: list[dict] = []
    for o in outputs or []:
        if not isinstance(o, dict):
            continue
        c = output_check.check_output(src_meta, o)
        row = {k: o.get(k) for k in _MEASURED if k in o}
        if o.get("error"):
            # 워커가 산출을 읽지 못한 오류 원문(ffprobe 마지막 줄).
            # 진단용이며 화면 사유에는 쓰지 않는다.
            row["probe_error"] = str(o["error"])[:300]
        rows.append({
            "label": c["label"], **row,
            "expected_seconds": c["expected_seconds"],
            "delta_seconds": c["delta_seconds"],
            "tolerance_seconds": output_check.DURATION_TOLERANCE_S,
            "cause": c["cause"],
        })
    if not rows:
        # 측정값이 없는 경우. 측정하지 않은 산출은 실패로 세지 않는다.
        return OutputVerdict(measured=None, cause=None, error=None)
    bad = next((r for r in rows if r["cause"]), None)
    if bad is None:
        return OutputVerdict(measured=rows[0], cause=None, error=None)
    return OutputVerdict(
        measured=bad, cause=bad["cause"],
        error=error_text.output_check_text(
            bad["cause"], output=name,
            delta_seconds=bad["delta_seconds"], tolerance_seconds=bad["tolerance_seconds"]),
        transient=bad["cause"] in output_check.TRANSIENT_CAUSES,
    )


def discard_rejected(bucket: str, key: str) -> None:
    """검사에 떨어진 산출을 지운다. 삭제에 실패해도 예외를 내지 않는다.

    최종 키에 남기면 깨진 사본이 정상 산출의 키에 남고, 스테이징 키에 남기면 정리하는 곳이 없어
    계속 과금된다. 지우지 못해도 실패 판정과 사유는 그대로 둔다.

    주의: 자기 토큰으로 실패를 기록한 뒤에만 부른다.
    낡은 보고로 지우면 새 시도의 정상 산출이 지워진다.
    """
    try:
        storage.delete_object(bucket=bucket, key=key)
    except Exception:  # noqa: BLE001 — 치우기 실패가 판정을 뒤집지 않는다
        log.warning("검사에 떨어진 산출 삭제 실패 key=%s", key)


async def complete_transcode(
    db: AsyncSession, video_id: UUID, token: str,
    *, ok: bool, error: str | None = None, transient: bool = False,
    elapsed_s: float | None = None, outputs: list | None = None,
) -> bool:
    """워커의 완료 보고를 판정한다. 자기 토큰이 아직 유효할 때만 받아들인다(펜싱).

    성공하면 이 시도의 스테이징 산출(`staging_key`)을 마스터 키로 옮기고 `done`으로 바꾼 뒤
    다음 단계를 연다. 옮기지 못하면 영구 오류로 센다. `analysis_uri`는 publish가 채운다.
    일시 오류는 `pending`으로 돌리고 시도를 환불한다. 연속 횟수에 따라 간격을 늘리고
    (`_RETRY_AFTER_KEY`), `_TRANSIENT_LIMIT`번 이어지면 `failed`로 확정한다.
    영구 오류는 `_MAX_RETRIES`까지 재시도한 뒤 `failed`로 확정한다.

    워커가 성공을 보고해도 `outputs`(분석본 측정값)를 원본과 비교한다(`judge_outputs`).
    어긋나면 영구 오류로 처리한다.
    검사 자체가 시간을 넘긴 경우만 서버 부하일 수 있어 일시 오류로 본다.
    영구 오류도 상한까지는 자동으로 다시 돈다. 읽다가 끊긴 입력은 다시 돌면 해소되기 때문이다.
    상한에 닿은 뒤에는 `retry`로 되살린다. 검사에 떨어진 산출은 스테이징에서 지운다.

    통과한 산출의 측정값은 `extra["transcode_output"]`에 병합한다.
    `outputs`가 없으면 검사 없이 받는다.
    """
    video = await db.get(Video, video_id)
    if video is None:
        return False

    # 산출 버킷은 행을 다시 쓰기 전에 읽어 둔다. 아래 갱신·병합 뒤에 `extra`를 읽으면 만료된 속성을
    # 동기 경로에서 다시 읽게 되어 실패한다.
    await storage.ensure_loaded(db, video.extra)
    out_bucket = derived_bucket(video)
    meta = (video.extra or {}).get("probe") or {}
    verdict = judge_outputs(meta, outputs, name="분석본") if ok else None
    if verdict is not None and verdict.cause is not None:
        # 산출 검사에 떨어지면 워커의 성공 보고를 실패 경로로 돌린다.
        ok, transient, error = False, verdict.transient, verdict.error
    measured = verdict.measured if verdict is not None else None

    if ok:
        bucket = out_bucket
        made = plan_outputs(meta, video.transcode_profile)
        # 마스터는 스테이징에 두고 최종 분석본 위치로는 publish가 옮긴다.
        # 그 사이에 비식별화가 들어간다.
        # `analysis_uri`를 지금 채우면 비식별화 전 파일을 MI가 읽는다.
        row = (await db.execute(
            update(Video)
            .where(
                Video.video_id == video_id,
                Video.transcode_token == token,
                Video.transcode_status == "running",
            )
            .values(
                transcode_status="done",
                transcode_token=None,
                transcode_lease_until=None,
            )
            .returning(Video.video_id)
        )).first()
        if row:
            try:
                # 이 시도가 올린 마스터만 옮긴다. 낡은 시도의 늦은 업로드는 자기 키에 남는다.
                promote_staged(bucket, staging_key(video_id, token, _MASTER_FILE),
                               master_key(video_id))
            except Exception as exc:  # noqa: BLE001 — 옮기지 못한 결과를 성공으로 접지 않는다
                # 예외가 태스크 밖으로 나가면 트랜잭션이 롤백되고 리스가 끝난 뒤 처음부터 다시 돈다.
                # 원인이 영구적이면 그 순환이 끝나지 않으므로 영구 오류로 세어 상한에서 멈춘다.
                await db.rollback()
                log.error("transcode 마스터 승격 실패 video=%s: %s: %s",
                          video_id, type(exc).__name__, exc)
                return await complete_transcode(
                    db, video_id, token, ok=False,
                    error="결과 파일을 저장소에 올리지 못했습니다", transient=False)
            await db.refresh(video)
            # 다음 단계는 `open_next`가 정한다.
            # 비식별화가 켜져 있으면 publish 대신 deident가 열린다.
            from api_gateway.services.media.pipeline import open_next
            await open_next(db, video)
            await video_service.merge_extra(db, video_id, {
                "transcode": {
                    "master_key": master_key(video_id),
                    "outputs": made,
                    "bucket": bucket,
                    "reason": needs_transcode(meta, video.transcode_profile),
                    "src": {"codec": meta.get("video_codec"), "height": meta.get("height")},
                    # 프리셋을 바꾼 뒤 재처리 범위를 정할 수 있게 사용한 프리셋을 남긴다.
                    "presets": {"analysis": _ANALYSIS_PRESET, "playback": _PLAYBACK_PRESET},
                    "elapsed_s": elapsed_s,
                },
                # 분석본 측정값은 최상위 키로 둔다. `merge_extra`는 최상위 키 단위로 병합하고
                # 재생본 확정이 `transcode` 키를 통째로 다시 쓰므로 `transcode` 안에 두면 사라진다.
                **({"transcode_output": measured} if measured else {}),
            }, remove=(_ERROR_KEY, _STREAK_KEY, _RETRY_AFTER_KEY))
            # 화면이 분석본·재생본을 항목으로 나눠 표시할 수 있게 산출 계획을 이력에 남긴다.
            await stage_runs.finish(
                db, video_id, "transcode", ok=True,
                detail={
                    "analysis_height": _ANALYSIS_HEIGHT,
                    "src_height": meta.get("height"),
                    "master_key": master_key(video_id),
                    # 저장 위치를 남긴다. 스테이징은 임시 영역이라 다음 단계가 쓰고 지운다.
                    "stored": "staging",
                    # 계획에 없어 만들지 않은 산출과 실패해 만들지 못한 산출을 화면이 구분하는 근거.
                    "outputs": made,
                    # 산출 측정값과 원본 대비 차이. 화면의 검사 항목이 읽는다.
                    **({"measured": measured} if measured else {}),
                },
            )
        await db.commit()
        if not row:
            log.warning("transcode 펜싱 거부 — 낡은 완료 보고 폐기 video=%s", video_id)
            # 낡은 시도의 스테이징 파일은 옮길 곳이 없고
            # 앱 경로에는 버킷 만료 규칙이 없어 여기서 지운다.
            # 키에 그 시도의 토큰이 있어 현재 시도의 키와 겹치지 않는다.
            discard_rejected(out_bucket, staging_key(video_id, token, _MASTER_FILE))
        return row is not None

    # `extra`의 사유에만 `[일시]` 표식을 붙인다.
    # 목록의 사유 문장이 이 표식으로 일시 오류를 구분한다.
    # 단계 이력은 일시 여부를 `detail.transient`에 따로 싣고 화면이 그 값으로
    # `일시 오류` 줄을 그리므로 사유 문장에 표식을 붙이지 않는다.
    said = (error or "사유를 받지 못했습니다.")[:400]
    msg = f"{'[일시] ' if transient else ''}{said}"
    # 산출 검사 위반은 위반 코드(`cause`, 집계용)와 수치(`measured`, 원인 추적용)를 이력에 싣는다.
    check = ({"cause": verdict.cause, "measured": measured}
             if verdict is not None and verdict.cause else {})
    # 일시 오류는 시도를 소모하지 않고 연속 횟수를 센다(`deident.complete`와 같은 규칙).
    streak = int((video.extra or {}).get(_STREAK_KEY) or 0) + 1 if transient else 0
    capped = transient and streak >= _TRANSIENT_LIMIT
    if capped:
        msg = f"{said.rstrip('.')}. 일시 오류가 {streak}회 이어져 자동 재시도를 멈췄습니다."
    if (not transient and video.transcode_attempts >= _MAX_RETRIES) or capped:
        row = (await db.execute(
            update(Video)
            .where(Video.video_id == video_id, Video.transcode_token == token,
                   Video.transcode_status == "running")
            .values(transcode_status="failed", transcode_token=None,
                    transcode_lease_until=None)
            .returning(Video.video_id)
        )).first()
        if row:
            await stage_runs.finish(db, video_id, "transcode", ok=False, error=msg,
                                    detail=check or None)
            await video_service.merge_extra(
                db, video_id, {_ERROR_KEY: msg}, remove=(_STREAK_KEY, _RETRY_AFTER_KEY))
            if check:
                discard_rejected(out_bucket, staging_key(video_id, token, _MASTER_FILE))
            log.error("transcode 확정 실패(%s %d회) video=%s: %s",
                      "일시 오류 연속" if capped else "시도",
                      streak if capped else video.transcode_attempts, video_id, msg)
        await db.commit()
        return False
    row = (await db.execute(
        update(Video)
        .where(Video.video_id == video_id, Video.transcode_token == token,
               Video.transcode_status == "running")
        .values(
            transcode_status="pending",
            transcode_token=None,
            transcode_lease_until=None,
            # 일시 오류는 실행 시점의 문제라서 시도 횟수를 되돌린다.
            transcode_attempts=Video.transcode_attempts - (1 if transient else 0),
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
                db, video_id, "transcode", ok=False, error=said,
                detail={"transient": True, "streak": streak, "retry_after": retry_at, **check})
            await video_service.merge_extra(db, video_id, {
                _ERROR_KEY: msg, _STREAK_KEY: streak, _RETRY_AFTER_KEY: retry_at})
        else:
            # 영구 오류가 나면 일시 오류의 연속 횟수를 초기화한다.
            await stage_runs.finish(
                db, video_id, "transcode", ok=False, error=said,
                detail={"transient": False, **check})
            await video_service.merge_extra(
                db, video_id, {_ERROR_KEY: msg}, remove=(_STREAK_KEY, _RETRY_AFTER_KEY))
        if check:
            # 검사에 떨어진 마스터는 시도마다 키가 달라 다음 시도가 덮어쓰지 않으므로 여기서 지운다.
            discard_rejected(out_bucket, staging_key(video_id, token, _MASTER_FILE))
        log.warning("transcode %s — pending 복귀 video=%s: %s",
                    "일시(환불)" if transient else "실패(재시도)", video_id, msg)
    await db.commit()
    return False


async def retry(db: AsyncSession, project_id: UUID, video_id: UUID, user: User) -> None:
    """`failed`로 확정된 변환을 다시 큐에 넣는다. project_admin이 부른다.

    영구 오류의 자동 재시도는 `_MAX_RETRIES`번까지다.
    이 함수는 상한에 닿은 뒤 사람이 되살리는 경로다.
    규격 재검사(`probe/reset`)는 규격 검사를 통과한 영상을 `409 not_resettable`로 거절하므로
    변환만 실패한 영상은 이 경로로 되살린다.

    `failed` 상태에서만 받는다(`deident.retry`와 같은 방식).
    처리 중(`pending`·`running`)에 다시 넣으면 같은 영상에 잡이 두 개 돈다.
    멈춘 잡은 `reclaim_expired`가 되살린다.
    끝난 변환(`done`·`skipped`)을 다시 돌리면
    다음 단계(비식별화·재생본)가 이미 쓴 마스터를 덮어쓴다.

    시도 횟수를 0으로 되돌린다. 되돌리지 않으면 다음 실패 한 번에 곧바로 `failed`가 된다.

    분석본 변환이 실패했으면 분석본 변환을, 아니면 실패한 재생본 변환을 되살린다.
    화면은 둘을 `변환 재시도` 하나로 표시한다. 재생본 쪽 열은 `publish.reset_failed`가 되돌린다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            분석본·재생본 변환 모두 실패 상태가 아닌 경우(409 `transcode_not_failed`).
    """
    video = await db.get(Video, video_id)
    if not video or video.project_id != project_id or video.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_project_admin(db, project_id, user)
    if video.transcode_status == "failed":
        stage = "transcode"
        before = ((video.extra or {}).get(_ERROR_KEY) or "")[:200]
        video.transcode_status = "pending"
        video.transcode_attempts = 0
        video.transcode_token = None
        video.transcode_lease_until = None
        # 진행률의 분자도 비운다. 남기면 새 시도가 시작하자마자 지난 시도의 퍼센트가 표시된다.
        video.transcode_done_seconds = None
        await db.flush()
        # 실패 기록과 일시 오류 연속 횟수·재시도 시각을 지운다.
        # 남기면 목록이 실패 사유를 계속 표시하고, 다시 넣은 영상이 앞선 연속 횟수 때문에
        # 곧바로 멈추거나 재시도 시각까지 투입되지 않는다.
        await video_service.merge_extra(
            db, video_id, {}, remove=(_ERROR_KEY, _STREAK_KEY, _RETRY_AFTER_KEY))
    elif video.publish_status == "failed":
        from api_gateway.services.media import publish as publish_service
        stage = "publish"
        before = await publish_service.reset_failed(db, video)
    else:
        raise HTTPException(status.HTTP_409_CONFLICT, "transcode_not_failed")
    audit.emit(
        db, event_type="video.transcode_retried", target_kind="video", target_id=video_id,
        actor_user_id=user.user_id, project_id=project_id,
        # 어느 인코딩을 되살렸는지 남긴다. 화면은 한 이름으로 부르지만 기록에서는 나눈다.
        payload={"previous_error": before, "stage": stage},
    )
    await db.commit()


async def reclaim_expired(db: AsyncSession, *, revoke: Revoke | None = None) -> int:
    """응답이 끊긴 시도를 회수한다. 5분 주기 beat가 부른다.

    `running`인데 리스가 만료되면 워커가 비정상 종료된 것으로 보고 `pending`으로 되돌린다.
    `queued`인데 `updated_at`이 오래됐으면 큐 메시지가 유실된 것으로 보고 `pending`으로 되돌린다.

    시도 횟수(`transcode_attempts`)는 투입 때 올린다.
    시작 하트비트가 온 뒤 회수된 시도는 시도로 센다.
    워커 자식 프로세스를 죽이는 입력(메모리 초과, 디코더 충돌)은 실패 보고 없이 끝나므로
    여기서 세지 않으면 끝없이 다시 돈다. 상한(`_MAX_RETRIES`)에 닿으면 `failed`로 확정한다.
    시작 하트비트가 오지 않은 시도(진행 초가 비어 있음)는 처리 서버가 잡을 가져가지 못한 경우라서
    시도를 되돌리고 다른 사유를 적는다.

    회수한 시도의 옛 토큰은 커밋한 뒤 `revoke`로 넘긴다. 워커가 큐에 남은 옛 메시지를 시작 전에 버려
    같은 영상을 두 번 변환하지 않는다.
    """
    now = datetime.now(UTC)
    # 지울 토큰을 먼저 읽는다. UPDATE의 RETURNING은 바뀐 값(빈 토큰)을 돌려준다.
    dead = (await db.execute(
        select(Video.video_id, Video.transcode_token, Video.transcode_done_seconds,
               Video.transcode_attempts)
        .where(Video.transcode_status == "running", Video.transcode_lease_until < now)
        .with_for_update(skip_locked=True)
    )).all()
    for vid, _token, done, attempts in dead:
        if done is not None and attempts >= _MAX_RETRIES:
            note = crash_cap_note(attempts)
            await db.execute(
                update(Video).where(Video.video_id == vid)
                .values(transcode_status="failed", transcode_token=None,
                        transcode_lease_until=None))
            await stage_runs.finish(db, vid, "transcode", ok=False, error=note,
                                    detail={"transient": False, "reclaimed": True})
            await video_service.merge_extra(db, vid, {_ERROR_KEY: note})
            log.error("transcode 확정 실패(시작 뒤 회수 %d회) video=%s", attempts, vid)
            continue
        await db.execute(
            update(Video).where(Video.video_id == vid)
            .values(transcode_status="pending", transcode_token=None,
                    transcode_lease_until=None,
                    transcode_attempts=Video.transcode_attempts - (1 if done is None else 0)))
        await stage_runs.finish(
            db, vid, "transcode", ok=False,
            # 화면에 표시되는 사유라 설계 용어 `리스` 대신 일어난 일을 쓴다.
            error=(_NOT_STARTED_NOTE if done is None
                   else "처리 서버 응답이 끊겼습니다. 잠시 후 다시 시도합니다."),
            detail={"transient": True},
        )
    stale = (await db.execute(
        update(Video)
        .where(
            Video.transcode_status == "queued",
            Video.updated_at < now - timedelta(seconds=_QUEUED_STALE_S),
        )
        .values(transcode_status="pending")
        .returning(Video.video_id)
    )).all()
    await db.commit()
    _revoke(revoke, [t for _, t, _, _ in dead if t])
    n = len(dead) + len(stale)
    if n:
        log.warning(
            "transcode 회수 — 리스 만료 %d건, queued 유실 %d건 → pending 복귀",
            len(dead), len(stale),
        )
    return n
