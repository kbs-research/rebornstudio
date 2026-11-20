"""grievance_messages: 라벨러와 운영자의 여러 차례 대화.

기존 grievances.resolution(처리 코멘트 하나)을 첫 운영자 메시지로 백필해 대화가 이어지게 한다.

Revision ID: 0032
Revises: 0031
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grievance_messages",
        sa.Column("message_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("grievance_id", UUID(as_uuid=True),
                  sa.ForeignKey("grievances.grievance_id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("status_change", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status_change IS NULL OR status_change IN ('in_review','resolved','dismissed')",
            name="ck_grievance_message_status",
        ),
    )
    op.create_index(
        "ix_grievance_messages_grievance", "grievance_messages", ["grievance_id", "created_at"]
    )
    # 기존 처리 코멘트(resolution)를 첫 운영자 메시지로 백필해 대화 기록을 남긴다.
    op.execute(
        """
        INSERT INTO grievance_messages (message_id, grievance_id, author_id, body, status_change, created_at)
        SELECT gen_random_uuid(), grievance_id, resolved_by, resolution,
               CASE WHEN status IN ('in_review','resolved','dismissed') THEN status ELSE NULL END,
               COALESCE(resolved_at, created_at)
        FROM grievances
        WHERE resolution IS NOT NULL AND resolution <> ''
        """
    )


def downgrade() -> None:
    op.drop_index("ix_grievance_messages_grievance", table_name="grievance_messages")
    op.drop_table("grievance_messages")
