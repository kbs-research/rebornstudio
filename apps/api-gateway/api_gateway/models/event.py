"""감사 로그와 통합 이벤트 outbox를 함께 담는 `domain_events` 테이블.

행을 추가만 한다(append-only).
`kind`가 이벤트 성격을 구분한다. audit은 사람이 나중에 읽는 기록이다.
integration은 다른 도메인이 구독하는 사실이다.
integration 이벤트는 상태 변경과 같은 트랜잭션에서 쓴다(outbox).
구독자가 없어서 integration 이벤트는 전달하지 않고 쌓기만 한다.
"""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base


class DomainEvent(Base):
    """이벤트 한 건. 행은 보존 기간 정리(`services/ops/audit_retention`)만 지운다."""

    __tablename__ = "domain_events"

    event_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id")
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    project_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.project_id", ondelete="SET NULL")
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 이벤트 성격. audit(기본) 또는 integration. 구분 기준은 모듈 docstring에 있다.
    kind: Mapped[str] = mapped_column(
        String(12), nullable=False, default="audit", server_default="audit"
    )
