"""모든 ORM 모델이 상속하는 SQLAlchemy Declarative Base와 공통 mixin."""
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """모든 ORM 모델의 기반 클래스. alembic이 이 클래스의 메타데이터로 스키마를 비교한다."""

    pass


class TimestampMixin:
    """`created_at`·`updated_at` 열을 더하는 mixin. 두 값 모두 DB 시각(`now()`)으로 채운다."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
