"""검수 초안: 판정 전까지 입력한 내용을 서버에 저장한다.

지적 문장, 반려 사유, 수정 후 승인 캡션이 판정 버튼을 누르기 전까지 화면에만 있으면 탭이 비정상 종료되거나
access 토큰(60분)이 끊길 때 그대로 사라진다. 라벨링에는 임시 저장과 자동 저장이 있으므로 검수에도 같은 저장을 둔다.

라벨러의 `draft`와 컬럼을 나눈다. 한 컬럼을 라벨러와 검수자가 함께 쓰면 서로의 초안을 덮어쓴다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_items", sa.Column(
        "review_draft", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("work_items", "review_draft")
