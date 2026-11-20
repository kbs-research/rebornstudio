"""영상을 나눈 구간(클립)을 담는 `clips` 테이블.

MAIU reconciler, 씬 분할, 수동 입력이 클립을 만든다. 생성 코드는 `services/media/clip.py` 한 곳에 둔다.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base

# 클립 상태(`rights_status`). 다섯 값 가운데 `auto_cleared`·`legal_review`·`rejected` 셋만 쓴다.
# 나머지 둘은 RebornStudio Pro와 같은 스키마를 유지하려고 남긴다.
ClipRightsStatus = Literal[
    "auto_cleared", "cleared", "cleared_with_edit", "rejected", "legal_review",
]


class Clip(Base):
    """클립 한 건. 클립 상태(`rights_status`)를 함께 둔다.

    클립은 정상 상태(`auto_cleared`)로 생긴다. 비식별화 미흡 신고는 `legal_review`,
    그 신고를 제외로 처리하면 `rejected`가 된다.
    """

    __tablename__ = "clips"
    __table_args__ = (
        # 미판정(`pending`)은 만들지 않는 값이지만 RebornStudio Pro와 같은 스키마를 유지하려고 DB 제약에 남긴다.
        CheckConstraint(
            "rights_status IN ('pending','auto_cleared','cleared',"
            "'cleared_with_edit','rejected','legal_review')",
            name="ck_clip_rights_status",
        ),
        CheckConstraint("end_seconds > start_seconds", name="ck_clip_time_range"),
    )

    clip_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    video_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("videos.video_id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False,
    )
    start_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    end_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    auto_caption: Mapped[dict | None] = mapped_column(JSONB)
    speakers: Mapped[list | None] = mapped_column(JSONB)
    rights_status: Mapped[ClipRightsStatus] = mapped_column(
        String(30), nullable=False, default="auto_cleared", server_default="auto_cleared")
    maiu_job_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    # 비식별화 신고를 처리한 사람·시각·사유. 컬럼 이름은 RebornStudio Pro 스키마와 같다.
    rights_decided_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    rights_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rights_decision_reason: Mapped[str | None] = mapped_column(String)
    rights_decision_comment: Mapped[str | None] = mapped_column(String)
    extra: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
