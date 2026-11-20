"""검수 반려 때 지적한 문장을 구조화해 저장한다.

- reviews.flagged_sentences JSONB: 반려할 때 검수자가 지적한 문장 텍스트 목록.
  재작업 워크벤치가 이 목록으로 해당 문장을 빨간색으로 미리 표시한다.
  comment 문자열('지적 문장 —「…」')을 화면에서 파싱하면 형식이 쉽게 깨지므로 구조화한 컬럼에 둔다.
  nullable이고 기존 행은 NULL이다.

Revision ID: 0037
Revises: 0036
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reviews", sa.Column("flagged_sentences", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("reviews", "flagged_sentences")
