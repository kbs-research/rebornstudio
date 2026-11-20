"""반려 사유 목록. 기본 목록(`BUILTIN`)을 고정해 쓴다.

저장된 목록이 있으면 프로젝트 설정(`settings.reject_reasons`)에서 읽는다.
설정 화면이 없어 새로 쓰지 않는다. 반려에는 코드 배열을 저장하고(`reviews.reason_codes`),
자유 입력 문장은 `comment`에 따로 남긴다. 관리자는 어떤 반려가 가장 많은지 코드로 세어 보고
가이드와 교육을 고친다. 고른 사유 이름을 이어 붙인 자유 문자열로 저장하면 사유별로 셀 수 없다.

규칙은 다음과 같다.

- 코드는 바꾸지 않는다. 꺼진 사유는 새 반려에 쓸 수 없지만 이력에는 계속 이름으로 표시된다.
- 입력 기능이 없어진 사유는 목록에서 빼고 이름만 `RETIRED_LABELS`에 남긴다. 지난 반려의 이력은
  그 이름으로 표시된다.
- 기본 목록은 라벨러가 고칠 수 있는 사유만 켠다. 영상 메타는 운영자가 입력하므로
  `메타 누락`은 라벨러가 고칠 필드가 없는 사유다. 기존 반려 데이터가 이 코드를 쓰므로 꺼진
  상태로 남긴다.

`work`(판정 검증)·`org`(설정 응답)·`analytics`·`export_service`(완료 이력)가
같은 목록을 읽는다. 어느 도메인 패키지에 두어도 import-linter 허용 목록을 늘려야 한다.
그래서 평면 모듈로 둔다.
"""
from __future__ import annotations

import re
from typing import Final

from fastapi.exceptions import RequestValidationError
from reborn_contracts import (
    REJECT_REASON_CODE_PATTERN,
    RejectReasonDTO,
    RejectReasonRef,
)

SETTINGS_KEY: Final = "reject_reasons"

#: 기본 반려 사유 목록. 순서가 검수 화면의 칩 순서다. 이름은 화면에서 쓰는 말로 짓는다.
BUILTIN: Final[tuple[RejectReasonDTO, ...]] = (
    RejectReasonDTO(code="caption_inaccurate", label="캡션 부정확"),
    RejectReasonDTO(code="deident_insufficient", label="비식별화 미흡"),
    # 영상 메타는 운영자가 영상에서 입력한다. 라벨러가 고칠 필드가 없는 사유라 끈다.
    # 기존 반려 데이터(백필)가 이 코드를 쓴다. 꺼진 사유도 이력에 표시되므로 목록에 남긴다.
    RejectReasonDTO(code="metadata_missing", label="메타 누락", enabled=False),
)

#: 목록에서 뺀 사유의 이름. 사유가 가리키던 입력 기능이 없어 새 반려에는 쓸 수 없다.
#: 코드는 바꾸지 않으므로 지난 반려가 이 코드를 계속 가진다. 이력 표시에만 이 이름을 쓴다.
RETIRED_LABELS: Final[dict[str, str]] = {
    "sentence_range": "문장 구간 오류",
    "speaker_name": "화자명 오류",
    "tag_inappropriate": "태그 부적절",
}

CUSTOM_PREFIX: Final = "custom_"
_CODE_RE = re.compile(REJECT_REASON_CODE_PATTERN)


def of_project(settings: dict | None) -> list[RejectReasonDTO]:
    """프로젝트의 사유 목록을 돌려준다. 저장된 목록 뒤에 저장본에 없는 기본 코드를 붙인다.

    저장된 목록은 저장한 순서 그대로 둔다. 기본 코드는 항상 있다. 코드는 지우지 않으므로,
    저장본에 없는 기본 코드는 저장한 뒤에 새로 생긴 코드다. 이 코드는 기본값(켜짐·꺼짐) 그대로
    끝에 붙인다. 깨진 항목은 버리고 나머지를 돌려준다. 그래서 설정 항목 하나가 깨져도 판정
    전체가 실패하지 않는다. 저장본에 남은 `RETIRED_LABELS` 코드도 버린다.
    """
    raw = (settings or {}).get(SETTINGS_KEY)
    stored: list[RejectReasonDTO] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for x in raw:
            if not isinstance(x, dict):
                continue
            try:
                r = RejectReasonDTO(**x)
            except ValueError:
                continue
            if (r.code in seen or r.code in RETIRED_LABELS or not _CODE_RE.match(r.code)
                    or not r.label.strip()):
                continue
            seen.add(r.code)
            stored.append(r)
    return stored + [r.model_copy() for r in BUILTIN if r.code not in seen]


def _raise(errors: list[tuple[tuple, str]]) -> None:
    """422를 요청 검증 실패와 같은 모양으로 던진다. 화면이 입력란 옆에 사유를 표시한다.

    화면의 `api()`는 배열 `detail`을 `validation_error`와 `fields`로 바꾸고, 한국어 `msg`는
    그대로 쓴다(`validationItemText`). 서비스가 따로 문자열 코드를 만들면 어느 필드인지
    가리킬 수 없다.
    """
    raise RequestValidationError([
        {"type": "value_error", "loc": ("body", *loc), "msg": msg, "input": None}
        for loc, msg in errors
    ])


def validate_decision(
    decision: str, codes: list[str], catalog: list[RejectReasonDTO], reason: str | None = None,
) -> list[str]:
    """판정 요청의 사유 코드를 검증하고, 중복을 뺀 코드를 원래 순서대로 돌려준다.

    사유 코드는 반려에만 붙는다. 승인에 사유 코드가 오면 모순된 요청이라 422로 거절한다
    (판정 규칙은 서버 한 곳에 둔다).
    반려에는 사유 코드나 보충 설명(`reason`) 중 하나가 있어야 한다. 지적 문장은 틀린 위치만
    가리킨다. 사유가 없으면 라벨러가 재작업에서 무엇을 고칠지 알 수 없다.
    화면의 반려 제출과 같은 조건이다.

    Raises:
        RequestValidationError: 반려가 아닌데 코드가 왔거나, 목록에 없거나 꺼진 코드인 경우,
            반려에 사유 코드도 보충 설명도 없는 경우(422).
    """
    if decision == "reject" and not codes and not (reason or "").strip():
        _raise([(("reason_codes",), "반려 사유를 고르거나 보충 설명을 적어주세요")])
    if not codes:
        return []
    if decision != "reject":
        _raise([(("reason_codes",), "반려 사유는 반려 판정에만 붙습니다")])
    by_code = {r.code: r for r in catalog}
    errors: list[tuple[tuple, str]] = []
    out: list[str] = []
    for i, c in enumerate(codes):
        r = by_code.get(c)
        if r is None:
            errors.append((("reason_codes", i), "목록에 없는 반려 사유입니다"))
        elif not r.enabled:
            errors.append((("reason_codes", i), f"꺼진 반려 사유입니다({r.label})"))
        elif c not in out:
            out.append(c)
    if errors:
        _raise(errors)
    return out


def refs(codes: list[str] | None, catalog: list[RejectReasonDTO]) -> list[RejectReasonRef]:
    """저장된 코드를 코드와 현재 목록의 이름으로 바꾼다. 꺼진 사유도 이름으로 표시된다.

    목록에서 뺀 사유는 `RETIRED_LABELS`의 이름으로 표시한다. 어디에도 없는 코드는 이름 대신
    코드를 표시한다. 숨기면 사유가 알림 없이 사라지므로 코드를 남긴다.
    """
    by_code = {**RETIRED_LABELS, **{r.code: r.label for r in catalog}}
    return [RejectReasonRef(code=c, label=by_code.get(c, c)) for c in (codes or [])]
