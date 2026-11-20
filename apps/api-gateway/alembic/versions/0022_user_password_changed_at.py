"""users.password_changed_at를 더한다.

비밀번호를 바꾸면 기존 세션을 무효로 하고, 재설정 토큰을 한 번만 쓰게 하는 데 보조로 쓴다.

Revision ID: 0022
Revises: 0021
"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """users에 password_changed_at를 더한다. 이 시각 이전에 발급된 access token은 무효다."""
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "password_changed_at")
