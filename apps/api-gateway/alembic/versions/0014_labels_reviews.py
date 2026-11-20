"""labels와 reviews 테이블을 더해 라벨 개정 이력과 검수 판정을 따로 담는다.

Revision ID: 0014
Revises: 0013

work_item.payload는 기존 코드 호환용 최신 스냅숏으로 남긴다.
labels는 parent_label_id로 이은 전체 개정 체인을 담고, reviews는 판정마다 한 행을 남긴다.
검수 화면의 이전 버전 비교(diff)와 개정 이력은 이 두 테이블을 읽는다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "labels",
        sa.Column("label_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("work_item_id", UUID(as_uuid=True),
                  sa.ForeignKey("work_items.work_item_id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("parent_label_id", UUID(as_uuid=True),
                  sa.ForeignKey("labels.label_id", ondelete="SET NULL")),
        sa.Column("submitted_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("label_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("diff_chars_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_labels_work", "labels", ["work_item_id", "submitted_at"])
    op.create_index("idx_labels_parent", "labels", ["parent_label_id"])

    op.create_table(
        "reviews",
        sa.Column("review_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("work_item_id", UUID(as_uuid=True),
                  sa.ForeignKey("work_items.work_item_id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("label_id", UUID(as_uuid=True),
                  sa.ForeignKey("labels.label_id", ondelete="SET NULL")),
        sa.Column("reviewer_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "verdict IN ('approve','reject','modify_and_approve')",
            name="ck_review_verdict",
        ),
    )
    op.create_index("idx_reviews_label", "reviews", ["label_id"])
    op.create_index("idx_reviews_work", "reviews", ["work_item_id", "reviewed_at"])


def downgrade() -> None:
    op.drop_index("idx_reviews_work", table_name="reviews")
    op.drop_index("idx_reviews_label", table_name="reviews")
    op.drop_table("reviews")
    op.drop_index("idx_labels_parent", table_name="labels")
    op.drop_index("idx_labels_work", table_name="labels")
    op.drop_table("labels")
