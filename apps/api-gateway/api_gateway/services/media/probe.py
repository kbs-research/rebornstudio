"""MI 요청 전에 ffprobe로 규격을 검사하는 게이트(워커 배치).

분석 비용을 아끼려고 둔다. 메타 추출은 MI가 맡는다.
해상도·재생 시간·fps는 Naver MI가 `import-status.metaData`로 주고
reconciler가 저장한다(`services/maiu/reconciler/ingest.py`).
MI는 시간당 과금이고 그 메타는 분석을 돌린 뒤에야 나온다.
깨진 파일, 지원하지 않는 파일, 상한을 넘는 파일을 분석 뒤에 알면 분석 비용이 낭비되므로
MI 요청 전에 적은 비용으로 걸러 낸다.

설계 결정은 다음과 같다.
  - 워커 배치로 비동기 실행한다(썸네일 배치와 같은 방식).
    등록 요청 경로에서 동기로 돌리면 NCP 폴더 단위 대량 등록이 느려진다. 등록은 바로 응답한다.
  - 파일의 헤더만 읽는다.
    presigned URL을 ffprobe에 그대로 넘기면 HTTP Range 요청으로 헤더만 읽는다
    (측정 근거는 `reborn_transcode/probe.py` docstring). 파일 전체는 트랜스코딩만 내려받는다.
  - `rejected`와 `error`를 구분한다.
    규격 위반(용량·길이·코덱)은 다시 검사해도 결과가 같아 `rejected`로 확정 차단한다.
    probe 자체가 실패하면 `error`로 둔다. `error`도 분석 요청을 막지만 재검사로 풀린다.
    망 순단·스로틀·5xx·제한 시간 초과는 `error`로 확정하지 않고 백오프 뒤 다시 검사한다.
    연속 일시 오류가 `_MAX_TRANSIENT`번을 넘으면 `error`로 확정한다. 상한이 없으면 저장소가
    계속 응답하지 않는 영상을 끝없이 다시 검사한다.
    읽지 못한 파일은 변환·재생본 단계가 열리지 않는다(`pipeline.open_next`는 통과 뒤에만 불린다).
    그래서 원본이 그대로 MI로 나가고, 깨진 파일이면 MI가 거절한다.
    일시적인 문제였으면 운영자가 재검사(`video.reset_probe`)로 다시 돌린다.
  - probe가 읽은 duration·size로는 비어 있는 열만 채운다. MI가 채운 메타를 덮어쓰지 않는다.
  - 배치는 `single_runner`로 하나만 돈다. 겹친 실행이 같은 영상을 함께 검사하면 단계 기록과
    감사 이벤트가 두 번 남는다. 영상마다 커밋해 배치 중간의 실패가 앞선 결과를 되돌리지 않게 한다.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import Video
from api_gateway.services import audit, error_text
from api_gateway.services.joblock import single_runner
from api_gateway.services.media import storage
from api_gateway.services.media.video import _parse_s3_uri
from api_gateway.services.transient import is_transient_boto, is_transient_ffmpeg

log = logging.getLogger(__name__)

#: 배치 단일 실행 락 키('RBPR'). 락 획득·해제 규약은 `services/joblock.py`에 있다.
_LOCK_KEY = 0x52425052
# 일시 오류 연속 횟수와 다음 검사 시각을 담는 `extra` 키.
_STREAK_KEY = "probe_transient_streak"
_RETRY_AFTER_KEY = "probe_retry_after"
# `error`로 확정하기 전까지 허용하는 연속 일시 오류 횟수.
# 백오프가 1분에서 두 배씩 늘어 여섯 번이면 약 한 시간을 기다린다.
_MAX_TRANSIENT = 6
_BASE_BACKOFF_S = 60
_MAX_BACKOFF_S = 3600


def _codec_whitelist() -> set[str]:
    """설정의 쉼표 구분 코덱 목록을 set으로 바꾼다. 빈 값이면 빈 set이고 코덱 검사를 건너뛴다."""
    raw = (settings.probe_codec_whitelist or "").strip()
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


def _positive(v: Any) -> float | None:
    """JSONB에서 읽은 값이 양수이면 그 값을, 아니면 None을 돌려준다."""
    if isinstance(v, bool) or not isinstance(v, int | float) or v <= 0:
        return None
    return v


def bit_rate(meta: dict[str, Any] | None, video: Video | None = None) -> int | None:
    """화면에 보여 줄 영상 비트레이트(bit/s)를 정한다.

    ffprobe가 읽은 값(`bit_rate`)을 먼저 쓴다. 값이 없는 probe 결과도 있어서 그때는
    용량과 재생 시간으로 평균 비트레이트를 계산한다. ffprobe도 컨테이너에 비트레이트가 없으면
    같은 식(용량×8÷재생 시간)으로 계산하므로 두 값의 뜻이 같다.
    영상 상세(`video.dto`)가 이 함수로 계산한다. 화면이 따로 계산하면 값이 어긋난다.

    Args:
        meta: probe 결과 또는 단계 기록의 상세. 없으면 None.
        video: `meta`에 용량이나 재생 시간이 없을 때 값을 가져올 영상.
            영상의 probe 결과(`extra["probe"]`)를 먼저 보고 그다음 열을 본다.

    Returns:
        비트레이트(bit/s). 측정값이 없고 용량이나 재생 시간도 없으면 None.
    """
    m = meta or {}
    rate = _positive(m.get("bit_rate"))
    if rate is not None:
        return int(rate)
    src = ((video.extra or {}).get("probe") or {}) if video is not None else {}
    size = (_positive(m.get("size_bytes")) or _positive(src.get("size_bytes"))
            or _positive(video.file_size_bytes if video is not None else None))
    dur = (_positive(m.get("duration_seconds")) or _positive(src.get("duration_seconds"))
           or _positive(video.duration_seconds if video is not None else None))
    if size is None or dur is None:
        return None
    return round(size * 8 / dur)


def check_spec(meta: dict[str, Any]) -> list[str]:
    """probe 결과를 규격에 대조해 위반 사유 목록을 돌려준다. 통과하면 빈 목록이다.

    None(컨테이너에 기록되지 않은 값)은 상한 초과로 판정할 근거가 없어 통과로 본다.
    코덱 허용 목록이 비어 있으면 코덱 검사를 건너뛴다. 설정으로 코덱 검사를 끄는 방법이다.
    """
    violations: list[str] = []

    size = meta.get("size_bytes")
    if size is not None and size > settings.probe_max_bytes:
        violations.append(f"size_exceeds_limit ({size} > {settings.probe_max_bytes})")

    dur = meta.get("duration_seconds")
    if dur is not None and dur > settings.probe_max_duration_seconds:
        violations.append(f"duration_exceeds_limit ({dur} > {settings.probe_max_duration_seconds})")

    allowed = _codec_whitelist()
    codec = (meta.get("video_codec") or "").lower()
    if allowed and codec and codec not in allowed:
        violations.append(f"codec_not_allowed ({codec})")

    # 컨테이너 검사. MI 규격표는 MP4·MKV·MOV·WEBM만 허용한다.
    # ffprobe는 `mov,mp4,m4a,3gp,3g2,mj2`처럼 이름을 여러 개 돌려주므로
    # 허용 목록과의 교집합으로 판정한다.
    containers = _container_whitelist()
    fmt = {f.strip().lower() for f in (meta.get("format_name") or "").split(",") if f.strip()}
    if containers and fmt and not (fmt & containers):
        violations.append(f"container_not_allowed ({meta.get('format_name')})")

    # 해상도 검사. 상한 초과는 변환이 720p로 낮춰 해소하고, 하한 미달은 업스케일해도
    # 화질 정보가 늘지 않아 해소할 수 없다. 그래서 사유를 나눠 기록한다.
    height = meta.get("height")
    if height:
        if height > settings.probe_max_height:
            violations.append(f"resolution_too_high ({height}p > {settings.probe_max_height}p)")
        elif height < settings.probe_min_height:
            violations.append(f"resolution_too_low ({height}p < {settings.probe_min_height}p)")

    return violations


def _container_whitelist() -> set[str]:
    """설정의 쉼표 구분 컨테이너 목록을 set으로 바꾼다. 빈 값이면 컨테이너 검사를 건너뛴다."""
    raw = (settings.probe_container_whitelist or "").strip()
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


# 변환으로 해소할 수 있는 위반. h264/mp4/720p 사본을 만들면 해소된다.
# 크기·길이 초과와 해상도 미달은 자르거나 늘리는 단계가 없어 이 목록에서 뺀다.
REDEEMABLE_VIOLATIONS = ("codec_not_allowed", "container_not_allowed", "resolution_too_high")


def _pending_conditions() -> tuple:
    """probe 배치 후보의 조건을 만든다.

    대상은 `probe_status='pending'`이고 업로드가 끝났으며 `source_uri`가 있는 영상이다.
    일시 오류 뒤 재시도를 기다리는 영상은 재시도 시각이 지나야 들어온다.
    재시도 시각을 현재 시각과 비교하므로 부를 때마다 새로 만든다.
    """
    retry_after = Video.extra[_RETRY_AFTER_KEY].astext
    return (
        Video.probe_status == "pending",
        Video.upload_status == "uploaded",
        Video.source_uri.is_not(None),
        Video.deleted_at.is_(None),
        or_(retry_after.is_(None),
            cast(retry_after, DateTime(timezone=True)) <= datetime.now(UTC)),
    )


async def process_pending(db: AsyncSession, limit: int = 20) -> int:
    """probe 대기 영상을 배치로 검사하고 상태를 바꾼 건수를 돌려준다.

    다른 실행이 배치를 돌리는 중이면 0을 돌려준다.
    후보는 id로 읽고 영상마다 다시 읽어 커밋한다. 한 건이 예상하지 못한 예외로 실패하면
    그 건만 되돌리고 나머지를 계속 검사한다.

    Returns:
        int: 상태가 바뀐 영상 수(passed·rejected·error의 합). 재시도로 미룬 영상은 세지 않는다.
    """
    async with single_runner(_LOCK_KEY) as got:
        if not got:
            return 0
        ids = list(await db.scalars(
            select(Video.video_id).where(*_pending_conditions())
            .order_by(Video.created_at).limit(limit)
        ))
        changed = 0
        for video_id in ids:
            video = await db.get(Video, video_id)
            if video is None or video.probe_status != "pending":
                continue
            try:
                changed += 1 if await _probe_one(db, video) else 0
                await db.commit()
            except Exception:  # noqa: BLE001 — 한 건의 실패가 배치를 멈추지 않게 한다
                await db.rollback()
                log.exception("probe 처리 실패 video=%s", video_id)
        return changed


def _is_transient(exc: BaseException) -> bool:
    """probe 실패가 다시 검사하면 풀릴 수 있는 일시 오류이면 참이다."""
    from reborn_transcode import ProbeError, ProbeTimeoutError

    if isinstance(exc, ProbeTimeoutError):
        return True
    if isinstance(exc, ProbeError):
        return is_transient_ffmpeg(str(exc))
    return is_transient_boto(exc)


async def _defer(db: AsyncSession, video: Video, exc: BaseException) -> bool:
    """일시 오류로 실패한 영상을 대기로 두고 백오프 뒤 다시 검사하게 한다. 미뤘으면 참이다.

    연속 일시 오류가 `_MAX_TRANSIENT`번에 닿았으면 미루지 않고 거짓을 돌려준다.
    그러면 호출자가 `error`로 확정한다.
    """
    from api_gateway.services.media import stage_runs
    from api_gateway.services.media.video import merge_extra

    streak = int((video.extra or {}).get(_STREAK_KEY, 0)) + 1
    if streak > _MAX_TRANSIENT:
        return False
    delay = min(_BASE_BACKOFF_S * 2 ** (streak - 1), _MAX_BACKOFF_S)
    retry_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
    await stage_runs.finish(
        db, video.video_id, "probe", ok=False, error=error_text.operator_text(exc),
        detail={"transient": True, "streak": streak, "retry_after": retry_at})
    await merge_extra(db, video.video_id, {_STREAK_KEY: streak, _RETRY_AFTER_KEY: retry_at})
    log.info("probe 일시 오류 video=%s 연속 %d회 %ds 뒤 재시도: %s",
             video.video_id, streak, delay, exc)
    return True


async def _probe_one(db: AsyncSession, video: Video) -> bool:
    """영상 1건을 probe하고 상태와 메타를 반영한다. 상태를 바꿨으면 True를 돌려준다.

    ffprobe·presign 실패는 예외를 다시 던지지 않는다. 일시 오류는 대기로 두고(`_defer`)
    나머지는 `error` 상태로 기록한다.
    """
    # api 이미지에는 ffprobe와 reborn_transcode 소스가 없어 함수 안에서 import한다(worker-io 전용).
    from reborn_transcode import ProbeError, is_black_video, probe, sample_luma

    from api_gateway.services.media import stage_runs
    await stage_runs.start(db, video.video_id, "probe")

    try:
        # source_uri(s3://bucket/key)로 presigned GET을 만든다. 서명은 `storage`에서만 한다.
        bucket, key = _parse_s3_uri(video.source_uri or "")
        url = storage.presign_get_for(bucket=bucket, key=key)
        meta = probe(url, timeout=settings.probe_timeout_seconds)
    except ProbeError as e:
        if _is_transient(e) and await _defer(db, video, e):
            return False
        # 화면에는 조치할 수 있는 운영자용 사유만 싣고, 원문(ffprobe stderr·URL 끝부분)은
        # `error_raw`로 감사 이벤트에 남긴다. `str(e)`를 그대로 실으면 준비 타임라인에
        # `ffprobe failed (rc=1): https://…`가 표시된다.
        await _apply(db, video, status="error", detail={
            "error": error_text.operator_text(e), "error_raw": error_text.raw_detail(e, 500),
        })
        return True
    except Exception as e:  # presign 실패 등. 배치를 멈추지 않게 잡는다
        if _is_transient(e) and await _defer(db, video, e):
            return False
        # `ClientError` 같은 예외 타입 이름은 사유 문장에 넣지 않고 원문(`error_raw`)에만 남긴다.
        await _apply(db, video, status="error", detail={
            "error": error_text.operator_text(e),
            "error_raw": f"{type(e).__name__}: {str(e)[:400]}",
        })
        return True

    # 검은 화면 검사. 규격은 정상인데 재생하면 검은 화면만 나오는 영상은 MI 분석도 성공해서
    # 사람이 클립을 열어 볼 때에야 발견된다. 그때는 분석 비용이 이미 청구됐으므로
    # 몇 지점만 짧게 디코딩해 휘도를 잰다.
    # 휘도를 재지 못하면(ffmpeg 없음·실패) 검사를 건너뛰고 검은 화면으로 판정하지 않는다.
    lumas = sample_luma(url, meta.get("duration_seconds") or 0.0,
                        timeout=settings.probe_timeout_seconds)
    if lumas is not None:
        meta = {**meta, "luma_samples": [[round(a, 1), round(m, 1)] for a, m in lumas]}

    violations = check_spec(meta)
    if is_black_video(lumas):
        violations = [*violations, f"black_video (표본 {len(lumas or [])}개가 모두 검은 화면)"]
    # 변환으로 해소할 수 있는 위반(`REDEEMABLE_VIOLATIONS`)만 있으면
    # 거부하지 않고 트랜스코딩으로 해소한다.
    # 단 profile=never면 원본이 그대로 분석에 나가므로 거부한다.
    from api_gateway.services.media.transcode import needs_transcode
    redeem = needs_transcode(meta, video.transcode_profile)
    detail: dict[str, Any] = {**meta, "violations": violations}
    redeemable = bool(violations) and all(
        v.startswith(REDEEMABLE_VIOLATIONS) for v in violations
    )
    if redeemable and redeem:
        detail["transcode_redeemed"] = violations   # 변환 사유를 감사할 수 있게 원래 판정을 남긴다
        detail["violations"] = []
        violations = []
    await _apply(db, video, status="rejected" if violations else "passed", detail=detail)
    # 통과하면 `open_next`로 파이프라인의 다음 단계를 연다. `open_next`는 skipped 단계만 연다.
    # 다음 단계는 단계 순서를 정의한 `media/pipeline.py`가 정한다.
    # 여기서 직접 고르면 단계가 늘 때 한 곳만 빠뜨려도 그 경로의 영상이 오류 없이 멈춘다.
    if not violations:
        from api_gateway.services.media.pipeline import open_next
        await open_next(db, video)
    # probe가 읽은 값으로 빈 열만 채운다. MI가 채운 메타를 덮어쓰지 않는다.
    if video.duration_seconds is None and meta.get("duration_seconds") is not None:
        video.duration_seconds = meta["duration_seconds"]
    if video.file_size_bytes is None and meta.get("size_bytes") is not None:
        video.file_size_bytes = meta["size_bytes"]
    return True


async def _apply(
    db: AsyncSession, video: Video, *, status: str, detail: dict[str, Any]
) -> None:
    """probe 상태와 상세를 반영하고 감사 이벤트를 남긴다.

    감사 이벤트는 rejected·error일 때만 남긴다.
    ffprobe가 도는 동안 썸네일·MAIU 워커가 같은 행의 extra에 키를 쓸 수 있어
    extra는 원자적으로 병합한다.
    결과가 확정되면 일시 오류 기록을 지운다. 남기면 재검사(`reset_probe`) 뒤의 첫 검사가
    지난 재시도 시각까지 미뤄지고 백오프가 이전 연속 횟수에 이어진다.
    """
    from api_gateway.services.media import stage_runs
    from api_gateway.services.media.video import merge_extra

    video.probe_status = status  # type: ignore[assignment]
    await merge_extra(db, video.video_id, {"probe": detail},
                      remove=(_STREAK_KEY, _RETRY_AFTER_KEY))
    # 단계 이력을 남긴다. rejected(규격 위반)와 error(읽기 실패)는 실패한 시도로 기록한다.
    # 화면이 `h264 · 1920×1080 · 47:55` 같은 실제 값을 표시하도록
    # 검사한 값 중 화면이 쓰는 키만 남긴다.
    # 검사가 늘면 아래 키 목록만 넓힌다.
    await stage_runs.finish(
        db, video.video_id, "probe", ok=(status == "passed"),
        error=None if status == "passed"
        else "; ".join(detail.get("violations") or []) or detail.get("error"),
        detail={
            k: detail[k]
            # luma_samples는 검은 화면 검사의 근거다. 화면이 "검은 화면 통과"를 표시할 때 쓴다.
            for k in ("video_codec", "width", "height", "duration_seconds", "bit_rate",
                      "luma_samples")
            if detail.get(k) is not None
        },
    )
    if status in ("rejected", "error"):
        audit.emit(
            db,
            event_type=f"video.probe_{status}",
            target_kind="video",
            target_id=video.video_id,
            actor_user_id=None,
            project_id=video.project_id,
            # reason은 화면과 같은 문구다. raw는 관리자가 원인을 추적할 원문이다.
            payload={
                "reason": detail.get("violations") or detail.get("error"),
                **({"raw": detail["error_raw"]} if detail.get("error_raw") else {}),
            },
        )
