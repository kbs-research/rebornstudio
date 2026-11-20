"""캡션 품질검수 패키지.

`rules`는 모델 없이 규칙으로 검사한다.
`adapters`는 검사 모델을 교체할 수 있게 하는 어댑터 계층이다.
"""
import re

from reborn_contracts import QcFinding, QcStage

from api_gateway.services.caption_qc import adapters, rules, settings
from api_gateway.services.caption_qc.rules import (
    check_caption,
    check_clip_length,
    check_deident,
    check_metadata,
    check_pii,
    check_profanity,
    from_delivery_blockers,
    qc_status_of,
    verdict_of,
)
from api_gateway.services.deident_gap import DEIDENT_BEFORE_ON, DEIDENT_OFF

__all__ = [
    "adapters",
    "rules",
    "settings",
    "check_caption",
    "check_metadata",
    "check_clip_length",
    "check_deident",
    "check_pii",
    "check_profanity",
    "from_delivery_blockers",
    "qc_status_of",
    "verdict_of",
]


#: 건너뛴 단계의 원인 코드별 현재 사유 문장. 읽을 때 저장된 문장 대신 쓴다.
#: 실행 기록은 실행 시점의 문장을 저장하므로,
#: 문구를 고쳐도 바뀌지 않는 원인 코드로 현재 문장을 고른다.
#: 한 코드가 여러 경우를 뜻하는 `deident_not_run`·`video_missing`은 넣지 않는다.
_CAUSE_TEXT: dict[str, str] = {
    "rules_flagged": settings.RULES_FLAGGED,
    "profanity_empty": settings.PROFANITY_EMPTY,
    "clip_length_unset": settings.CLIP_LENGTH_UNSET,
    "glossary_empty": settings.GLOSSARY_EMPTY,
    "model_unconfigured": settings.MODEL_UNCONFIGURED,
    "model_failed": settings.MODEL_FAILED,
    "deident_off": DEIDENT_OFF,
    "deident_before_on": DEIDENT_BEFORE_ON,
}

#: 원인 코드 없이 저장된 이전 사유 문장과 현재 문장의 대응. 뜻이 하나로 정해지는 문장만 옮긴다.
_LEGACY_REASON: dict[str, str] = {
    "규칙에 걸려 모델을 부르지 않았습니다": settings.RULES_FLAGGED,
    "모델을 부르지 못했습니다": settings.MODEL_FAILED,
}

#: 이전 지적 문장의 항목 이름을 현재 화면 이름(`rules.label_of`)으로 바꾸는 규칙.
#: `부가 라벨`·`영상 메타데이터`는 라벨링 화면에 없는 이름이라 작업자가 고칠 곳을 찾지 못한다.
_LEGACY_MESSAGE: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^부가 라벨 (\d+)개가 비어 있습니다"),
        r"세부 묘사 중 \1개 항목이 비어 있습니다",
    ),
    (re.compile(r"^영상 메타데이터에 "), "영상 메타에 "),
)


def stage_reason(stored: dict) -> str | None:
    """건너뛴 단계의 사유를 현재 문장으로 돌려준다. 등록되지 않은 원인은 저장된 문장을 돌려준다."""
    cause = stored.get("cause")
    if cause in _CAUSE_TEXT:
        return _CAUSE_TEXT[cause]
    reason = stored.get("reason")
    return _LEGACY_REASON.get(reason, reason) if reason else reason


def finding_message(stored: dict) -> str:
    """저장된 지적 문장의 항목 이름을 현재 화면 이름으로 바꿔 돌려준다."""
    msg = stored.get("message", "") or ""
    for pat, repl in _LEGACY_MESSAGE:
        msg = pat.sub(repl, msg)
    return msg


def qc_finding_dto(stored: dict) -> QcFinding:
    """저장된 지적을 화면용 DTO로 바꾼다. 단계 이름과 출처는 저장하지 않고 읽을 때 붙인다.

    리뷰어 근거와 보류함 두 화면이 같은 조립을 쓰도록 한 곳에 둔다.
    """
    check = stored.get("check", "")
    return QcFinding(
        check=check,
        severity=stored.get("severity", "warn"),
        message=finding_message(stored),
        sentence_id=stored.get("sentence_id"),
        check_label=rules.label_of(check),
        source=rules.source_of(check),
    )


def qc_stages_dto(stored: list | None) -> list[QcStage] | None:
    """저장된 단계 기록을 화면용으로 바꾼다. `None`은 그대로 `None`으로 돌려준다.

    `None`은 이 컬럼(0077)이 생기기 전의 실행이라 단계 기록이 없다는 뜻이다.
    빈 목록은 단계를 하나도 실행하지 않았다는 뜻이다. 화면은 두 경우를 다르게 표시한다.
    """
    if stored is None:
        return None
    return [
        QcStage(
            key=st.get("key", ""),
            label=rules.label_of(st.get("key", "")),
            status=st.get("status", "skipped"),
            reason=stage_reason(st) if st.get("status") == "skipped" else st.get("reason"),
            source=rules.source_of(st.get("key", "")),
        )
        for st in stored
    ]
