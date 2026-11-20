"""rights_decisions: 영상별 저작권 판단 이력.

Revision ID: 0004
Revises: 0003
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rights_decisions",
        sa.Column("decision_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True),
                  sa.ForeignKey("videos.video_id", ondelete="CASCADE"), nullable=False),
        sa.Column("decided_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "decision IN ('cleared','rejected','quarantine')",
            name="ck_rights_decision",
        ),
    )
    op.create_index("idx_rights_decisions_video", "rights_decisions", ["video_id", "decided_at"])


def downgrade() -> None:
    op.drop_table("rights_decisions")
