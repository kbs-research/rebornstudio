"""문의 대화 메시지에 이미지 첨부를 더한다.

- grievance_messages.attachments JSONB(기본 [])에 메시지별 첨부 이미지 목록 [{key,name}]을 담는다.

Revision ID: 0034
Revises: 0033
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "grievance_messages",
        sa.Column("attachments", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("grievance_messages", "attachments")
