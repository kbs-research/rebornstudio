"""반입 배치 id: 한 번에 올리거나 등록한 묶음을 다음 단계로 잇는 표식.

방금 올린 영상을 저작권 1단과 MI 단계에서 묶음으로 다시 찾게 한다.
반입 시각으로 묶으면 동시에 작업하는 사람의 영상이 섞이므로 명시적 id를 둔다.
기존 행은 NULL이다. 배치 없이 들어온 영상이라는 뜻이고, 필터를 걸지 않으면 평소처럼 모두 보인다.

Revision ID: 0044
Revises: 0043
"""
import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("upload_batch_id", sa.UUID(as_uuid=True), nullable=True))
    # 배치 필터는 항상 프로젝트 안에서 걸리므로 복합 인덱스 하나로 한 번에 스캔한다.
    op.create_index("ix_videos_project_batch", "videos", ["project_id", "upload_batch_id"])


def downgrade() -> None:
    op.drop_index("ix_videos_project_batch", table_name="videos")
    op.drop_column("videos", "upload_batch_id")
