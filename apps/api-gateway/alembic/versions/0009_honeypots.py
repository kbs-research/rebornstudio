"""honeypots: 정답이 알려진 work_item으로 어노테이터 품질을 잰다.

Revision ID: 0009
Revises: 0008
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "honeypots",
        sa.Column("honeypot_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_item_id", UUID(as_uuid=True),
                  sa.ForeignKey("work_items.work_item_id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("reference_payload", JSONB, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_honeypots_project", "honeypots", ["project_id"])


def downgrade() -> None:
    op.drop_table("honeypots")
