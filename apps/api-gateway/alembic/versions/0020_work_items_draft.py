"""work_items.draft JSONB(제출 전 임시 저장)를 더한다.

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """work_items에 nullable draft JSONB 컬럼을 더한다. 제출 payload와 따로 둔다."""
    op.add_column(
        "work_items",
        sa.Column("draft", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("work_items", "draft")
