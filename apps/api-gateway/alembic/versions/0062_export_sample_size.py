"""`exports.sample_size`를 더한다. 전량 납품 전에 몇 건짜리 샘플로 형식을 먼저 확인받는다.

`NULL`이면 전량이다. 이 컬럼 이전의 export는 모두 전량이었으므로 백필이 필요 없다.
"""
from alembic import op
import sqlalchemy as sa

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exports", sa.Column("sample_size", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("exports", "sample_size")
