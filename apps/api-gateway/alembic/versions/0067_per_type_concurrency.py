"""동시 보유 한도를 작업 종류마다 따로 둔다.

라벨링 한도처럼 검수·저작권 한도도 프로젝트 컬럼으로 둔다. 그러면 프로젝트마다 다르게 두고 배포 없이 바꿀 수 있다.

한 사람이 한 번에 점유하는 작업 수는 종류마다 성질이 다르다. 라벨링은 한 건이 길고, 저작권은 영상 하나에
클립이 수백 개라서 같은 5라도 부담이 다르다. 그래서 저작권 기본값은 3이다.

기존 프로젝트도 값을 갖도록 `server_default`를 준다. nullable로 두면 정하지 않은 경우와 3으로 정한 경우를
코드가 매번 구별해야 한다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column(
        "review_max_concurrent", sa.Integer(), nullable=False, server_default="5"))
    op.add_column("projects", sa.Column(
        "rights_max_concurrent", sa.Integer(), nullable=False, server_default="3"))


def downgrade() -> None:
    op.drop_column("projects", "rights_max_concurrent")
    op.drop_column("projects", "review_max_concurrent")
