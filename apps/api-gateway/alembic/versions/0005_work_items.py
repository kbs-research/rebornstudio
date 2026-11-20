"""work_items: 영상 단위 작업 큐와 TTL 기반 claim.

Revision ID: 0005
Revises: 0004
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column("work_item_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True),
                  sa.ForeignKey("videos.video_id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="available"),
        sa.Column("claimed_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id")),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("rejection_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('available','claimed','submitted','approved','rejected','quarantine')",
            name="ck_work_status",
        ),
        sa.UniqueConstraint("video_id", name="uq_work_item_video"),  # 영상 하나에 작업 하나
    )
    # 가져갈 수 있는 작업 조회. claim의 SKIP LOCKED 후보를 찾는다.
    op.create_index(
        "idx_work_available", "work_items", ["project_id", "created_at"],
        postgresql_where=sa.text("status = 'available'"),
    )
    # 만료된 claim을 정리할 후보 조회
    op.create_index(
        "idx_work_claimed_expires", "work_items", ["expires_at"],
        postgresql_where=sa.text("status = 'claimed'"),
    )
    # 작업자가 점유한 작업 조회
    op.create_index(
        "idx_work_my_claimed", "work_items", ["claimed_by"],
        postgresql_where=sa.text("status = 'claimed'"),
    )


def downgrade() -> None:
    op.drop_table("work_items")
