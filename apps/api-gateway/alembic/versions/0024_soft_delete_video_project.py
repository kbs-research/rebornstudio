"""영상과 프로젝트의 soft delete 컬럼 deleted_at을 더한다.

Revision ID: 0024
Revises: 0023
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """videos와 projects에 soft delete 기준 시각 deleted_at을 더한다."""
    op.add_column("videos", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "deleted_at")
    op.drop_column("videos", "deleted_at")
