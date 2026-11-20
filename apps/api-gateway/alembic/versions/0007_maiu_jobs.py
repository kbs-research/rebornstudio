"""maiu_jobs: 외부 MAIU에 위탁한 work_item 추적.

Revision ID: 0007
Revises: 0006
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "maiu_jobs",
        sa.Column("job_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_item_id", UUID(as_uuid=True),
                  sa.ForeignKey("work_items.work_item_id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("external_id", sa.String(100), unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="dispatched"),
        sa.Column("request_payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("response_payload", JSONB),
        sa.Column("error", sa.Text),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('dispatched','completed','failed','cancelled')",
            name="ck_maiu_job_status",
        ),
    )
    op.create_index("idx_maiu_jobs_project", "maiu_jobs", ["project_id", "dispatched_at"])
    op.create_index(
        "idx_maiu_jobs_pending", "maiu_jobs", ["dispatched_at"],
        postgresql_where=sa.text("status = 'dispatched'"),
    )


def downgrade() -> None:
    op.drop_table("maiu_jobs")
