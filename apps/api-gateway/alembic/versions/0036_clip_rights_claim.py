"""클립 저작권의 배타적 claim 필드를 더한다.

- clips.rights_claimed_by/at/expires_at: 저작권 검수자가 클립을 한 건씩 배타적으로 claim한다.
  라벨링·검수 워크벤치와 같은 방식이다. nullable이고 만료되면 워커가 회수하며 판정이 끝나면 해제한다.

Revision ID: 0036
Revises: 0035
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("rights_claimed_by", PgUUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=True))
    op.add_column("clips", sa.Column("rights_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("clips", sa.Column("rights_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("clips", "rights_expires_at")
    op.drop_column("clips", "rights_claimed_at")
    op.drop_column("clips", "rights_claimed_by")
