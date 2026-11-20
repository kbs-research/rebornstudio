"""VLM 학습 데이터 manifest를 만드는 내보내기 작업을 기록하는 `exports` 테이블."""
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base

# 내보내기 작업 상태.
ExportStatus = Literal["pending", "running", "completed", "failed"]


class Export(Base):
    """내보내기 작업 한 건. 워커가 처리하며 상태와 manifest 위치를 기록한다."""

    __tablename__ = "exports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_export_status",
        ),
    )

    export_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    status: Mapped[ExportStatus] = mapped_column(String(20), nullable=False, default="pending")
    # 검수용 샘플에 담을 건수. 전량이면 NULL이다.
    # 샘플을 납품본으로 오해하지 않도록 파일 안의 `dataset_card`·`report`에도 같은 사실을 적는다.
    sample_size: Mapped[int | None] = mapped_column(Integer)
    # True면 납품 메타데이터에서 라벨링 선택 항목 키를 뺀다(`export_package.OPTIONAL_LABEL_FIELDS`).
    # 파일 안의 `report.json`·`dataset_card.yaml`·`README.md`에도 뺀 키를 적는다.
    omit_optional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    manifest_s3_key: Mapped[str | None] = mapped_column(Text)
    #: 패키지를 올린 버킷. 비어 있으면 내려받기 주소를 만들지 않는다.
    storage_bucket: Mapped[str | None] = mapped_column(Text)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 실패·재시도 사유 코드(`export_service`의 `UPLOAD_RETRYING` 등).
    #: 완료된 행에는 제외한 건수를 `excluded:N`으로 적는다.
    #: 새 컬럼 대신 이 필드를 쓴다. 이 필드를 읽는 곳은 `_to_dto` 하나다.
    #: 오래된 행에는 예외 원문이 들어 있을 수 있어서 `_to_dto`가 일반 코드로 바꿔 화면에 보낸다.
    error: Mapped[str | None] = mapped_column(Text)
    #: 워커가 이 행을 가져간 횟수(첫 시도, 일시 오류 재시도, 워커 유실 뒤 회수 포함).
    #: 가져갈 때 올리므로 매번 워커를 비정상 종료시키는 건도 상한에서 멈춘다.
    #: 실패할 때 세면 워커가 비정상 종료될 때 트랜잭션과 함께 횟수도 롤백되어 같은 건을 끝없이 다시 처리한다.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
