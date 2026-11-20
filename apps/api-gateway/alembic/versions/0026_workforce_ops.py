"""작업자 운영용 project_members.standing과 grievances 테이블을 더한다.

Revision ID: 0026
Revises: 0025
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID  # noqa: F401

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """작업자 품질 standing 등급과 이의 제기·고충 채널을 더한다."""
    # 작업자 standing: 품질에 따라 자동으로 규제하거나 보상하는 등급
    op.add_column(
        "project_members",
        sa.Column("standing", sa.String(20), nullable=False, server_default="normal"),
    )
    op.create_check_constraint(
        "ck_member_standing",
        "project_members",
        "standing IN ('trusted','normal','watch','restricted')",
    )

    # 이의 제기와 고충(부당 반려·문제 클립·고충)
    op.create_table(
        "grievances",
        sa.Column("grievance_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("raised_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column(
            "work_item_id", UUID(as_uuid=True),
            sa.ForeignKey("work_items.work_item_id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("resolution", sa.Text, nullable=True),
        sa.Column("resolved_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('dispute','complaint','task_issue')", name="ck_grievance_kind"),
        sa.CheckConstraint(
            "status IN ('open','in_review','resolved','dismissed')", name="ck_grievance_status",
        ),
    )
    op.create_index("idx_grievance_project", "grievances", ["project_id", "status"])


def downgrade() -> None:
    op.drop_table("grievances")
    op.drop_constraint("ck_member_standing", "project_members", type_="check")
    op.drop_column("project_members", "standing")
