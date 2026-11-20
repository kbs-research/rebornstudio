"""users에 선택 개인정보 컬럼(전화·생년월일·주소)과 개인정보 수집·이용 동의 시각을 더한다.

Revision ID: 0031
Revises: 0030
"""
import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone_number", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("birth_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("privacy_consented_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "privacy_consented_at")
    op.drop_column("users", "address")
    op.drop_column("users", "birth_date")
    op.drop_column("users", "phone_number")
