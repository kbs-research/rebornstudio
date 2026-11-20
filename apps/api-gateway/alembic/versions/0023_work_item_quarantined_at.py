"""work_items.quarantined_at를 더한다.

격리 SLA 자동 처리의 기준 시각이다.

Revision ID: 0023
Revises: 0022
"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """work_items에 quarantined_at를 더한다. 격리에 들어간 시각으로 SLA 7일 만료를 판정한다."""
    op.add_column(
        "work_items", sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("work_items", "quarantined_at")
