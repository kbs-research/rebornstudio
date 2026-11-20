"""어댑터 단계를 중립 상태로 옮기는 매핑. 상태 전이(`job_state.advance`)가 쓴다."""
import logging

log = logging.getLogger(__name__)

#: MI 단계별 중립 상태. 모든 `MaiuStatus`가 이 표에 있어야 하고 테스트가 확인한다.
_NEUTRAL: dict[str, str] = {
    "pending": "pending",
    "registering": "running",
    "registered": "running",
    "analyzing": "running",
    "analyzed": "running",
    "summarizing": "running",
    "done": "done",
    "failed": "failed",
}


def neutral_status(maiu_status: str) -> str:
    """MI 단계를 어댑터 중립 상태로 옮긴다.

    표에 없는 단계는 시작했고 끝나지 않은 상태이므로 `running`으로 둔다.
    표를 고쳐야 하는 경우라서 경고 로그를 남긴다. 원래 값은 `stage`에 남아 진단에 쓸 수 있다.
    """
    if maiu_status not in _NEUTRAL:
        log.warning("모르는 MI 단계 — 중립 매핑에 없다: %s", maiu_status)
        return "running"
    return _NEUTRAL[maiu_status]


