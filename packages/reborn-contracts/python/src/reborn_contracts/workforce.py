"""작업자 운영 스키마. 본인 판정 목록을 다룬다.

도구는 사람에게 품질 등급을 매기지 않는다.
판단 자료(작업량 대비 반려)는 대시보드 DTO가 준다.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from reborn_contracts.review import RejectReasonRef


class MemberDecisionRow(BaseModel):
    """한 사람이 내린 검수 판정 한 건.

    판정 목록만 준다. 수·비율·소요 시간 같은 지표는 만들지 않는다.
    """
    #: 판정 기록의 식별자(`review_id`). 커서의 동점 키로 쓴다.
    decision_id: UUID
    decided_at: datetime
    #: 판정 값. `approve`·`reject`·`modify_and_approve`다.
    decision: str
    video_id: UUID
    video_title: str | None = None
    #: 판정 대상 클립.
    clip_id: UUID | None = None
    clip_start_seconds: float | None = None
    clip_end_seconds: float | None = None
    #: 판정 대상 작업.
    work_item_id: UUID | None = None
    #: 반려 사유 코드와 이름. 검수 반려에만 붙는다.
    reasons: list[RejectReasonRef] = Field(default_factory=list)
    #: 판정에 적은 의견. 없으면 `None`이다.
    comment: str | None = None


class MemberDecisionPage(BaseModel):
    """한 사람의 판정 목록 한 페이지와 전체 수. `total`은 이 페이지가 아니라 전체 수다.

    다음 페이지는 마지막 행의 (`decided_at`, `decision_id`)를 커서로 보내 받는다.
    """
    total: int
    items: list[MemberDecisionRow]
