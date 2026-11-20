"""단계 호출 예외를 일시 오류와 확정 실패로 나눠 잡에 반영하는 처리.

일시 오류와 확정 실패의 판정은 이 모듈에만 둔다.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from api_gateway.config import settings
from api_gateway.models import AnalysisJob
from api_gateway.services import audit, error_text
from api_gateway.services.analysis import job_state
from api_gateway.services.maiu.client import target_of_job
from api_gateway.services.maiu.naver_client import MaiuTransientError
from api_gateway.services.maiu.reconciler.common import TRANSIENT_MARK, _job_target
from api_gateway.services.media import stage_runs as media_stage_runs

# 로거 이름은 패키지 이름으로 둔다.
# 하위 모듈이 모두 같은 로거 이름으로 기록해야 로그 검색과 필터가 패키지 단위로 동작한다.
log = logging.getLogger(__package__)

# 일시 오류 백오프의 시작 간격과 상한(초). 5초마다 같은 job을 호출하면 요청량 제한이 풀리지 않는다.
_BASE_BACKOFF_S = 5
_MAX_BACKOFF_S = 120


#: 요청량 제한을 받은 분석 연결을 다음 사이클에도 기억하는 Redis 키의 접두사.
_THROTTLE_PREFIX = "maiu:throttle:"
#: 응답에 Retry-After가 없을 때 그 연결의 조회를 멈추는 시간(초).
_THROTTLE_DEFAULT_S = 30
#: 기억하는 시간의 상한(초). 잘못된 Retry-After 값이 그 연결의 분석을 오래 멈추지 않게 한다.
_THROTTLE_MAX_S = 300


def throttle_key(key: tuple[str | None, str | None]) -> str:
    """분석 연결 키(연결 ID, 워크스페이스)의 Redis 키 이름을 만든다."""
    return f"{_THROTTLE_PREFIX}{key[0]}:{key[1] or ''}"


def _remembered(key: tuple[str | None, str | None]) -> bool:
    """이 연결이 앞 사이클에서 요청량 제한을 받아 아직 조회를 멈춘 상태이면 True.

    Redis를 읽지 못하면 False다. 기억은 호출을 줄이는 장치라 읽지 못해도 폴링을 막지 않는다.
    """
    from api_gateway import redis_infra

    try:
        client = redis_infra.get_redis()
        return bool(client is not None and client.exists(throttle_key(key)))
    except Exception:  # noqa: BLE001 — 기억을 읽지 못해도 폴링은 계속한다
        return False


def _remember(key: tuple[str | None, str | None], retry_after: float | None) -> None:
    """이 연결의 조회를 Retry-After(없으면 `_THROTTLE_DEFAULT_S`)초 동안 멈추도록 기록한다."""
    from api_gateway import redis_infra

    ttl = int(min(max(retry_after or _THROTTLE_DEFAULT_S, 1), _THROTTLE_MAX_S))
    try:
        client = redis_infra.get_redis()
        if client is not None:
            client.set(throttle_key(key), "1", ex=ttl)
    except Exception:  # noqa: BLE001 — 기억을 남기지 못해도 이번 사이클의 게이트는 동작한다
        log.warning("MAIU 요청량 제한 기억 실패 connection=%s", key[0], exc_info=True)
        return
    log.warning("MAIU 요청량 제한 connection=%s — %d초 동안 이 연결을 조회하지 않음", key[0], ttl)


class ThrottleGate:
    """요청량 제한(429)을 받은 분석 연결의 남은 조회를 건너뛰는 게이트.

    잡별 백오프(`_on_stage_error`, 5초부터 두 배씩 최대 120초)는 429를 받은 잡만 미룬다.
    같은 연결의 다른 잡은 같은 배치에서 계속 조회된다. 그러면 진행 중인 분석이 많을수록
    한 사이클에 요청량 제한을 여러 번 받고 일시 오류 기록도 그만큼 쌓인다.
    그래서 429를 한 번 받으면 그 연결의 남은 잡은 이번 사이클에서 건너뛴다.
    건너뛴 잡은 다음 사이클에 다시 조회한다.
    이번 사이클에 호출하지 않았으므로 아무것도 기록하지 않는다.
    연결은 잡에 기록한 분석 선택(연결·워크스페이스)으로 구분한다. 다른 연결의 잡은 계속 조회한다.

    실제 분석 연결(분석 연동이 켜져 있고 연결 ID가 있는 잡)은 다음 사이클에도 막는다.
    Retry-After(없으면 30초) 동안 Redis 키(`throttle_key`)에 기록한다. 사이클은 5초마다 돌아서
    배치 안에서만 막으면 제한이 풀리기 전에 같은 연결에 다시 요청한다.
    가짜 클라이언트를 쓰는 잡은 실제 요청량 제한이 없어 배치 안에서만 막는다.
    """

    def __init__(self) -> None:
        """빈 상태로 만든다. 단계 함수가 배치마다 하나 만든다."""
        self._hit: set[tuple[str | None, str | None]] = set()
        self._checked: dict[tuple[str | None, str | None], bool] = {}

    @staticmethod
    def _shared(key: tuple[str | None, str | None]) -> bool:
        """이 연결의 요청량 제한을 다음 사이클까지 기억하면 True. 실제 분석 연결만 기억한다."""
        return bool(key[0]) and bool(getattr(settings, "maiu_enabled", False))

    @staticmethod
    def _key(job: AnalysisJob) -> tuple[str | None, str | None]:
        """잡이 쓰는 분석 연결의 키(연결 ID, 워크스페이스)를 만든다. 기록이 없으면 빈 키다."""
        t = target_of_job(job)
        return (str(t.connection_id) if t and t.connection_id else None,
                t.workspace if t else None)

    def blocked(self, job: AnalysisJob) -> bool:
        """이 잡의 연결이 이번 배치나 앞 사이클에서 요청량 제한을 받아 조회를 멈춘 상태이면 True.

        Redis 조회는 연결마다 배치에서 한 번만 한다.
        """
        key = self._key(job)
        if key in self._hit:
            return True
        if not self._shared(key):
            return False
        if key not in self._checked:
            self._checked[key] = _remembered(key)
        return self._checked[key]

    def note(self, job: AnalysisJob, exc: BaseException) -> None:
        """예외가 요청량 제한(429)이면 이 잡의 연결을 막는다. 다른 오류는 무시한다."""
        if isinstance(exc, MaiuTransientError) and exc.status == 429:
            key = self._key(job)
            if key in self._hit:
                return
            self._hit.add(key)
            if self._shared(key):
                _remember(key, exc.retry_after)


_STAGE_BY_CALLER = {
    "bulk_check_registrations": "register",
    "bulk_poll_registrations":  "poll_registration",
    "bulk_check_analyses":      "analyze",
    "bulk_poll_analyses":       "poll_analyze",
    "bulk_check_summaries":     "summary",
    "bulk_poll_scene_summaries": "poll_summary",
}


async def _on_stage_error(
    job: AnalysisJob, exc: Exception, *, db: AsyncSession | None = None,
) -> None:
    """단계 호출의 예외를 잡에 반영한다. 일시 오류는 실패로 확정하지 않는다.

    폴링은 같은 API를 반복해서 호출하므로 요청량 제한과 타임아웃이 정상 경로에서도 생긴다.
    요청량 제한과 타임아웃을 분석 실패로 처리하면 오래 걸리는 영상일수록 실패로 끝난다.

    일시 오류는 상태를 그대로 두고 `updated_at`만 갱신한다. 다음 사이클이 같은 job을 다시 처리한다.
    `updated_at`이 갱신되므로 stale sweep가 멈춘 job으로 오판하지 않는다.
    일시 오류는 응답을 받지 못한 경우라서 `attempt`(DTO의 retry_count)를 올리지 않고
    deferred 승격 기준에도 넣지 않는다.
    감사 이벤트와 경고 로그는 연속이 시작될 때(streak 1)만 남긴다. 연속 횟수와 마지막 원문은
    `payload`에 남는다. 사이클마다 남기면 같은 장애가 이어지는 동안 감사 기록과 로그가 그 줄로 찬다.

    단계 이름은 `inspect`로 호출한 함수 이름을 읽어 정한다(`_STAGE_BY_CALLER`).
    그래서 호출부가 단계 이름을 넘기지 않는다.
    """
    import inspect
    if isinstance(exc, MaiuTransientError):
        now = datetime.now(UTC)
        job.updated_at = now
        # 화면 세 곳이 이 문자열을 그대로 표시하므로 내부 엔드포인트를 넣지 않는다.
        # 원문은 아래 payload["last_error_raw"]에 남겨 관리자가 추적할 수 있게 한다.
        job.error_msg = f"{TRANSIENT_MARK} {error_text.operator_reason(exc)}"[:1000]
        # 지수 백오프. 일시 오류가 이어질 때마다 간격을 늘린다(5·10·20…최대 `_MAX_BACKOFF_S`).
        # 백오프 상태는 스키마 변경 없이 쓰도록 payload에 둔다.
        # streak는 같은 단계에서 연속으로 막힐 때만 늘린다.
        # 단계가 바뀌었으면 그 사이 호출이 성공했다.
        # 그때 이전 streak를 이어받으면 필요 이상으로 오래 기다린다.
        prev = job.payload or {}
        streak = (
            int(prev.get("transient_streak", 0)) + 1
            if prev.get("transient_stage") == job.stage
            else 1
        )
        delay = min(_BASE_BACKOFF_S * (2 ** (streak - 1)), _MAX_BACKOFF_S)
        job.payload = {
            **(job.payload or {}),
            "transient_streak": streak,
            "transient_stage": job.stage,
            "retry_after": (now + timedelta(seconds=delay)).isoformat(),
            "last_error_raw": error_text.raw_detail(exc, 400),
        }
        flag_modified(job, "payload")
        if streak > 1:
            log.debug("MAIU 일시 오류 %d회째 — 상태 유지: %s", streak, exc)
            return
        if db is not None:
            caller = inspect.stack()[1].function
            audit.emit(
                db, event_type="maiu.stage.transient_error", target_kind="maiu_job",
                target_id=_job_target(job), project_id=job.project_id,
                payload={
                    "asset_id": job.external_ref,
                    "stage": _STAGE_BY_CALLER.get(caller, "unknown"),
                    "job_stage": job.stage,
                    "http_status": exc.status,
                    "error": error_text.raw_detail(exc, 400),
                },
            )
        log.warning("MAIU 일시 오류 — 상태 유지하고 다음 사이클에 재시도: %s", exc)
        return

    prev_stage = job.stage
    job_state.advance(job, "failed")
    job.attempt += 1
    job.error_msg = error_text.operator_reason(exc)[:1000]
    # 원문은 화면에 표시하지 않지만 기록에는 남긴다. 그래야 관리자가 원인을 추적할 수 있다.
    job.payload = {**(job.payload or {}), "last_error_raw": error_text.raw_detail(exc, 400)}
    flag_modified(job, "payload")
    job.in_flight_count = max(0, job.in_flight_count - 1)
    # 단계 실행 기록에 어느 단계에서 확정 실패했는지 남긴다.
    # `stage_runs.finish`는 열린 running 행을 닫고, 열린 행이 없으면 시도 번호를 이어
    # 새 행을 넣는다. 닫힌 행을 여기서 직접 넣으면 시도 번호가 늘 1이 되고
    # 열린 running 행이 '진행 중'으로 남는다.
    _fail_stage = {
        "pending": "register", "registering": "register",
        "registered": "analyze", "analyzing": "analyze", "summarizing": "summarize",
    }.get(prev_stage)
    if db is not None and _fail_stage:
        # 시도 번호 규칙을 `stage_runs.finish` 한 곳에만 두어야
        # 단계 이력의 시도 번호가 실제 시도 횟수와 맞는다.
        await media_stage_runs.finish(
            db, job.video_id, _fail_stage, ok=False,
            error=error_text.operator_reason(exc)[:1000],
        )
    if db is None:
        return
    caller = inspect.stack()[1].function
    stage = _STAGE_BY_CALLER.get(caller, "unknown")
    audit.emit(
        db, event_type=f"maiu.stage.{stage}_failed", target_kind="maiu_job",
        target_id=_job_target(job), project_id=job.project_id,
        payload={
            "asset_id": job.external_ref,
            "retry_count": job.attempt,
            "prev_stage": prev_stage,
            # 화면과 달리 여기에는 원문을 남긴다. 경로·상태·본문이 있어야 원인을 추적할 수 있다.
            "error": error_text.raw_detail(exc, 400),
        },
    )
