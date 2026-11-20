"""라벨 수정 이력(revision chain)과 검수 이력을 담는 `labels`·`reviews` 테이블."""
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base

# 검수 판정 종류.
ReviewVerdict = Literal["approve", "reject", "modify_and_approve"]


class Label(Base):
    """라벨러가 제출한 라벨 한 건. 재제출하면 새 행을 만들고 이전 제출을 가리킨다."""

    __tablename__ = "labels"
    __table_args__ = (
        # 수정률 측정 필드 셋은 함께 있거나 함께 없다.
        # 초안 없이 거리만 있으면 무엇과 비교한 거리인지 알 수 없다.
        # 거리 없이 초안만 있으면 집계가 분모만 센다.
        CheckConstraint(
            "(ai_draft IS NULL AND edit_distance IS NULL AND draft_chars IS NULL)"
            " OR (ai_draft IS NOT NULL AND edit_distance IS NOT NULL"
            " AND draft_chars IS NOT NULL AND edit_distance >= 0 AND draft_chars > 0)",
            name="ck_label_edit_measure",
        ),
    )

    label_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    work_item_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("work_items.work_item_id", ondelete="CASCADE"),
        nullable=False,
    )
    # revision chain의 직전 제출. 첫 제출은 NULL이고, 이후 제출은 직전 Label.label_id를 가리킨다.
    parent_label_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("labels.label_id", ondelete="SET NULL")
    )
    submitted_by: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    label_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    diff_chars_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 수정률 측정값. 라벨러가 받은 AI 초안 원문, 그 초안 대비 편집 거리, 정규화한 초안 글자 수.
    # 작업의 첫 제출에만 채운다(`work.edit_rate`).
    # 비어 있으면 측정하지 않았다는 뜻이다(0과 구분한다).
    # 재제출, 검수자 수정본, 초안이 없던 클립이 여기에 해당한다.
    ai_draft: Mapped[str | None] = mapped_column(Text)
    edit_distance: Mapped[int | None] = mapped_column(Integer)
    draft_chars: Mapped[int | None] = mapped_column(Integer)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Review(Base):
    """검수 판정 한 건. 판정 종류, 반려 사유 코드, 지적한 문장을 담는다."""

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('approve','reject','modify_and_approve')",
            name="ck_review_verdict",
        ),
    )

    review_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    work_item_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("work_items.work_item_id", ondelete="CASCADE"),
        nullable=False,
    )
    label_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("labels.label_id", ondelete="SET NULL")
    )
    reviewer_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    verdict: Mapped[ReviewVerdict] = mapped_column(String(20), nullable=False)
    # 반려 사유 코드. 프로젝트 사유 목록(`services/reject_reasons`)의 코드만 저장한다.
    # 이름은 저장하지 않고 읽을 때 목록에서 붙인다. 그래서 이름을 고치면 이력에도 새 이름이 보인다.
    reason_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    # 자유 입력 사유 문장(보충 설명). 사유 칩은 `reason_codes`에 코드로 둔다.
    comment: Mapped[str | None] = mapped_column(Text)
    # 반려할 때 지적(flag)한 문장 텍스트. 재작업 워크벤치가 이 문장을 미리 빨간색으로 표시한다.
    # 코멘트 문자열을 파싱하지 않고 쓸 수 있게 구조화해 저장한다.
    flagged_sentences: Mapped[list | None] = mapped_column(JSONB)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
