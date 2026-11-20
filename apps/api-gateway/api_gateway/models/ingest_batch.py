"""한 번에 반입한 묶음을 별도 행으로 기록하는 `ingest_batches` 테이블."""
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base

# 반입 경로. upload는 사용자 컴퓨터에서 업로드, ncp는 NCP 버킷 등록이다.
IngestSource = Literal["upload", "ncp"]


class IngestBatch(Base):
    """반입 묶음 1건.

    id는 업로드 흐름을 바꾸지 않도록 클라이언트가 만든 값을 그대로 쓴다.
    서버는 그 묶음의 첫 영상을 받을 때 이 행을 만든다(`ensure_batch`).
    그래서 행이 없는 묶음 id가 생기지 않는다.
    이 행이 있어야 `videos.upload_batch_id`에 이름·만든 사람·출처가 붙고, 묶음을 목록에서 다시 찾을 수 있다.
    """

    __tablename__ = "ingest_batches"
    __table_args__ = (
        CheckConstraint("source IN ('upload','ncp')", name="ck_ingest_batch_source"),
        CheckConstraint(
            "import_status IS NULL OR import_status IN ('pending','running','done','failed')",
            name="ck_ingest_batch_import_status",
        ),
    )

    batch_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id")
    )
    source: Mapped[IngestSource] = mapped_column(String(20), nullable=False, default="upload")
    # 사람이 붙이는 이름(선택). 비어 있으면 화면이 시각·출처로 이름을 만들어 표시한다.
    label: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    # 폴더 단위 등록(서버 작업)에만 쓰는 필드. 일반 묶음은 셋 다 NULL이다.
    # spec은 잡 페이로드다: {prefix, bucket, copyright_policy, broadcaster, program_name, transcode_profile}.
    # 브라우저가 버킷을 순회하지 않도록 API는 여기에 사양만 남긴다.
    # worker beat가 pending 행을 가져가 서버에서 버킷을 순회하며 등록한다. transcode처럼 DB를 큐로 쓴다.
    import_spec: Mapped[dict | None] = mapped_column(JSONB)
    import_status: Mapped[str | None] = mapped_column(String(20))
    import_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
