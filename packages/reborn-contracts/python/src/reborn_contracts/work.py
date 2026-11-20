"""작업 배정 스키마."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from reborn_contracts.review import LabelDTO, RejectReasonRef, ReviewDecision, ReviewDTO

WorkStatus = Literal[
    "available", "claimed", "submitted", "approved", "rejected",
    # 종료된 작업. 비식별화 신고 제외, 영상 삭제로 닫힌 작업이다.
    # 큐에도, 집계 모집단에도 들어가지 않는다. 화면은 `종료`로 표시한다(`StatusBadge`).
    "cancelled",
]


class NextWorkResponse(BaseModel):
    """다음 작업 한 건(라벨링 `work/next`, 검수 `review/next`).

    한 사람은 역할마다 한 건씩 맡는다. 보유한 작업이 있으면 새로 배정하지 않고 그 작업을 돌려준다.
    원본 저장 경로는 싣지 않는다. 화면은 이 ID로 워크벤치를 연다.
    """
    work_item_id: UUID
    project_id: UUID
    video_id: UUID
    clip_id: UUID | None = None
    #: 이미 보유하던 작업을 다시 돌려줬으면 True, 이번에 새로 배정했으면 False.
    resumed: bool


class WorkSubmitRequest(BaseModel):
    """라벨링 결과 제출 요청."""
    payload: dict = Field(description="라벨링 결과(자유 형식)")


class WorkDraftRequest(BaseModel):
    """제출 전 작성 중인 라벨의 임시 저장 요청.

    30초마다 자동 저장할 때와 Ctrl+S를 누를 때 보낸다.
    """
    payload: dict = Field(description="작성 중 라벨 (제출 payload와 분리 저장)")


class DeidentMissReport(BaseModel):
    """비식별화 미흡 신고.

    라벨러가 화면에서 가려지지 않은 부분을 발견했을 때 보낸다.
    """
    timestamp_ms: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)


class WorkItemDTO(BaseModel):
    """작업(work_item) 한 건."""
    work_item_id: UUID
    video_id: UUID
    project_id: UUID
    clip_id: UUID | None = None        # reconciler가 만든 클립
    status: WorkStatus
    claimed_by: UUID | None = None
    claimed_at: datetime | None = None
    submitted_at: datetime | None = None
    review_claimed_by: UUID | None = None   # 검수자 배타 claim(검수 중 표시)
    rejection_count: int
    payload: dict
    draft: dict | None = None          # 제출 전 임시저장 복원용
    # 검수 초안. 라벨러의 `draft`와 필드를 나눈다. 한 필드를 둘이 쓰면 서로 덮어쓴다.
    review_draft: dict | None = None
    created_at: datetime


class MyWorkItemDTO(WorkItemDTO):
    """로그인한 사용자가 보유한 작업 한 건(`GET /work/me`). 재작업이면 마지막 반려 사유를 붙인다."""
    #: 가장 최근 반려의 사유 코드와 이름. 재작업(`rejection_count > 0`)이 아니면 빈 목록.
    last_reject_reasons: list[RejectReasonRef] = Field(default_factory=list)


class RepeatedRejectRow(BaseModel):
    """반려가 여러 번 쌓인 미완 작업 한 건. 관리자가 클립과 가이드를 살펴볼 근거를 싣는다."""
    work_item_id: UUID
    clip_id: UUID | None = None
    status: WorkStatus
    video_title: str | None = None
    clip_start_seconds: float | None = None
    clip_end_seconds: float | None = None
    #: 지금 보유한 라벨러의 이름. 보유자가 없으면 None.
    holder_name: str | None = None
    rejection_count: int
    #: 가장 최근 반려의 사유 코드와 이름.
    last_reject_reasons: list[RejectReasonRef] = Field(default_factory=list)
    #: 가장 최근 반려의 보충 설명.
    last_reject_comment: str | None = None
    last_rejected_at: datetime | None = None


class RepeatedRejectPage(BaseModel):
    """반려 누적 작업 목록. `total`은 조회 상한과 무관한 전체 수다."""
    #: 목록에 드는 최소 반려 횟수. 화면이 기준을 따로 적지 않고 이 값을 쓴다.
    threshold: int
    total: int
    items: list[RepeatedRejectRow]


#: 내 라벨링 기록 화면에서 호출한 사용자 기준으로 본 작업 상태.
#: 호출한 사용자가 아직 보유하면 작업 상태(`claimed`·`submitted`·`approved`·`rejected`)를
#: 그대로 쓴다. 다른 라벨러가 가져갔으면 `handed_over`, 대기열로
#: 돌아가 아무도 보유하지 않으면 `returned`, 닫힌 작업이면 `cancelled`다.
MyWorkState = Literal[
    "claimed", "submitted", "approved", "rejected", "cancelled",
    "handed_over", "returned",
]


class MyWorkHistoryRow(WorkItemDTO):
    """내 라벨링 기록 화면의 한 행. 작업의 현재 값에 호출한 사용자가 한 일을 따로 붙인다.

    `WorkItemDTO`의 시각과 상태는 작업의 현재 값이다. 작업이 신고·멤버 제거·재투입으로
    다른 라벨러에게 넘어가면 그 값은 다른 사람의 값이 된다. 그래서 화면은 `my_*` 필드로
    호출한 사용자의 제출 시각과 작업 시간을 그린다.
    `payload`·`draft`·`review_draft`는 비워서 보낸다. 넘어간 작업에서는 그 값이 다른 사람의
    입력이다.
    """
    history_at: datetime = Field(
        description=(
            "정렬과 커서에 쓰는 시각. 내 마지막 제출과 지금 보유 중이면 가져온 시각 중 늦은 것"
        )
    )
    my_state: MyWorkState
    my_submitted_at: datetime | None = Field(
        default=None, description="내 마지막 제출 시각. 가져오기만 하고 아직 제출하지 않았으면 None"
    )
    my_submit_count: int = Field(
        default=0, description="이 작업에 낸 내 제출 수 (반려 후 재제출 포함)"
    )
    my_started_at: datetime | None = Field(
        default=None,
        description=(
            "내 마지막 제출의 작업 시작 시각. 내가 가져온 시각과 반려 시각 중 제출 직전의 것이다. "
            "기록이 없으면 None"
        ),
    )
    my_work_seconds: float | None = Field(
        default=None, description="my_started_at부터 my_submitted_at까지 걸린 초. 잴 수 없으면 None"
    )
    my_verdict: ReviewDecision | None = Field(
        default=None, description="내 마지막 제출에 대한 가장 최근 판정. 아직 판정 전이면 None"
    )
    my_reasons: list[RejectReasonRef] = Field(
        default_factory=list, description="my_verdict가 반려일 때 사유 코드와 이름"
    )
    video_title: str | None = None
    clip_start_seconds: float | None = None
    clip_end_seconds: float | None = None


class MyWorkHistoryDetail(BaseModel):
    """내 라벨링 기록 화면의 상세. 호출한 사용자가 낸 라벨과 그 라벨의 검수만 싣는다.

    `/work/{id}/labels`는 지금 보유한 사람과 검수자에게만 열린다. 그래서 작업이 넘어간 뒤에는
    라벨러가 그 경로로 자기 기록을 볼 수 없다. 이 응답은 호출한 사람의 몫만 담는다.
    """
    row: MyWorkHistoryRow
    labels: list[LabelDTO]
    reviews: list[ReviewDTO]

