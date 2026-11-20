"""project_members.standing_locked와 notifications 테이블을 더한다.

Revision ID: 0027
Revises: 0026
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """standing 수동 지정 잠금과 작업자 인앱 알림을 더한다."""
    # 사람이 standing을 직접 정하면 일일 재계산이 덮어쓰지 않게 잠근다.
    op.add_column(
        "project_members",
        sa.Column("standing_locked", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "notifications",
        sa.Column("notification_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # 읽지 않은 알림을 먼저 찾는 인덱스
    op.create_index("idx_notification_user", "notifications", ["user_id", "read_at"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_column("project_members", "standing_locked")
