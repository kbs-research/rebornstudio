"""domain_events: 주요 mutation의 감사 로그.

Revision ID: 0008
Revises: 0007
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain_events",
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("actor_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("target_kind", sa.String(30), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True)),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="SET NULL")),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("idx_events_target", "domain_events", ["target_kind", "target_id"])
    op.create_index("idx_events_occurred", "domain_events", ["occurred_at"])
    op.create_index("idx_events_project", "domain_events", ["project_id", "occurred_at"])
    op.create_index("idx_events_type", "domain_events", ["event_type", "occurred_at"])


def downgrade() -> None:
    op.drop_table("domain_events")
