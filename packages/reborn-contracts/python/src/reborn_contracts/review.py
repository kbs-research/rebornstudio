"""검수 스키마. 검수자가 제출된 작업을 판정할 때 쓴다."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ReviewDecision = Literal["approve", "reject", "modify_and_approve"]

#: 반려 사유 코드의 형식. 영문 식별자다.
#: 프로젝트가 더한 사유에는 서버가 `custom_N` 코드를 붙인다.
REJECT_REASON_CODE_PATTERN = r"^[a-z][a-z0-9_]{1,39}$"


class RejectReasonRef(BaseModel):
    """반려에 붙은 사유 하나. 코드와 현재 사유 목록상의 이름을 담는다.

    이름은 저장하지 않고, 읽을 때 프로젝트 사유 목록에서 붙인다.
    그래서 이름을 고치면 이력도 새 이름으로 보인다.
    꺼진 사유도 이름으로 보인다. 목록에서 지우지 않고 끄기 때문이다.
    """
    code: str
    label: str


class RejectReasonDTO(BaseModel):
    """프로젝트 반려 사유 목록의 한 줄. 검수 화면 칩·설정 화면·통계가 같은 목록을 쓴다.

    `enabled=False`는 새 반려에 쓸 수 없다는 뜻이다. 사유 자체는 목록에 남는다.
    이미 쓰인 코드는 이력과 통계에서 계속 이름으로 보여야 하므로 지우지 않는다.
    """
    code: str
    label: str
    enabled: bool = True


class ReviewDecisionRequest(BaseModel):
    """검수 판정 요청. 승인, 반려, 수정 후 승인 중 하나를 보낸다."""
    decision: ReviewDecision
    #: 자유 입력 사유 문장(보충 설명). 사유 칩은 여기 이어 붙이지 않고 `reason_codes`로 보낸다.
    reason: str | None = Field(default=None, max_length=1000)
    #: 반려 사유 코드. 반려에만 싣는다. 프로젝트 목록에 없거나 꺼진 코드는 422다.
    #: 코드로 따로 받아야 사유별로 셀 수 있다.
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    # reject일 때 검수자가 지적(flag)한 문장 텍스트 목록.
    # 재작업 워크벤치가 해당 문장을 미리 빨간색으로 표시할 때 쓴다.
    # 코멘트 문자열을 파싱하지 않고 구조화해 저장하므로 형식이 바뀌어도 깨지지 않는다.
    flagged_sentences: list[str] | None = Field(default=None)
    # modify_and_approve일 때 검수자가 직접 수정한 라벨.
    # 직전 revision을 부모(parent)로 하는 새 Label revision을 INSERT한다.
    modified_label_data: dict | None = None
    #: 판정의 기준이 된 라벨(검수 화면이 보여 준 제출본)의 ID. 수정 후 승인에는 필수다.
    #: 서버는 이 값이 그 작업의 최신 라벨과 다르면 409로 거절한다.
    #: 다른 작업의 수정본이 이 작업의 승인본으로 저장되는 것을 막는다.
    base_label_id: UUID | None = None


class LabelDTO(BaseModel):
    """라벨 revision 체인의 한 행. `work_service.list_labels`의 응답 항목."""
    label_id: UUID
    work_item_id: UUID
    parent_label_id: UUID | None = None
    submitted_by: UUID
    label_data: dict
    diff_chars_count: int
    submitted_at: datetime
    # 라벨러가 받은 AI 초안 원문과, 초안 대비 편집 거리·초안 글자 수(정규화 뒤).
    # 작업의 첫 제출에만 있다(`work.edit_rate`).
    # 비어 있으면 재지 않았다는 뜻이다(0과 구분한다).
    # 측정 전 제출, 재제출, 검수자 수정본, 초안이 없던 클립이 여기에 해당한다.
    ai_draft: str | None = None
    edit_distance: int | None = None
    draft_chars: int | None = None


class ReviewDTO(BaseModel):
    """검수 판정 한 행. `review_service.list_reviews`의 응답 항목."""
    review_id: UUID
    work_item_id: UUID
    label_id: UUID | None = None
    reviewer_id: UUID
    verdict: ReviewDecision
    #: 자유 입력 사유 문장. 사유 칩은 `reasons`에 따로 있으므로 이 문자열을 나누지 않는다.
    comment: str | None = None
    #: 저장된 반려 사유 코드와, 읽을 때 목록에서 붙인 이름. 반려가 아니면 빈 배열이다.
    reason_codes: list[str] = Field(default_factory=list)
    reasons: list[RejectReasonRef] = Field(default_factory=list)
    flagged_sentences: list[str] = Field(default_factory=list)
    reviewed_at: datetime
