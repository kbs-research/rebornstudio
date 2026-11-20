"""저작권 초안: 전수 판정을 마치기 전까지 입력한 판정을 서버에 저장한다.

저작권 워크벤치는 영상의 모든 클립을 판정해야 제출된다.
판정이 화면에만 있으면 다른 화면에 다녀왔을 때 사라지고 서버의 클립은 모두 pending으로 남는다.

`work_items.review_draft`(0069)의 영상 버전이다. 판정 단위가 영상이라 초안도 영상에 둔다.
초안은 판정 또는 반납 때까지 유지하고, 그때 서버가 비운다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column(
        "rights_draft", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "rights_draft")
