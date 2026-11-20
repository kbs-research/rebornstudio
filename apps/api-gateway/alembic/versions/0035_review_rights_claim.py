"""검수·저작권의 배타적 claim 필드를 더한다.

- work_items.review_claimed_by/at/expires_at: 검수자의 배타 claim. 라벨러의 claimed_by와 별개다.
- videos.rights_claimed_by/at/expires_at: 저작권 검수자의 영상 단위 배타 claim.
모두 nullable이다. 만료되면 워커가 회수하고 판정이 끝나면 해제한다.

Revision ID: 0035
Revises: 0034
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_items", sa.Column("review_claimed_by", PgUUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=True))
    op.add_column("work_items", sa.Column("review_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("work_items", sa.Column("review_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("videos", sa.Column("rights_claimed_by", PgUUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=True))
    op.add_column("videos", sa.Column("rights_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("videos", sa.Column("rights_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "rights_expires_at")
    op.drop_column("videos", "rights_claimed_at")
    op.drop_column("videos", "rights_claimed_by")
    op.drop_column("work_items", "review_expires_at")
    op.drop_column("work_items", "review_claimed_at")
    op.drop_column("work_items", "review_claimed_by")
