"""announcements 테이블을 만든다.

관리자·시스템 공지를 담고, 대상은 전체 또는 개인이다.

Revision ID: 0030
Revises: 0029
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("announcement_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("severity", sa.String(10), nullable=False, server_default="info"),
        sa.Column("source", sa.String(10), nullable=False, server_default="manual"),
        sa.Column("target_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("severity IN ('info','warn','critical')", name="ck_announcement_severity"),
        sa.CheckConstraint("source IN ('manual','system')", name="ck_announcement_source"),
    )
    op.create_index("ix_announcements_project", "announcements", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_announcements_project", table_name="announcements")
    op.drop_table("announcements")
