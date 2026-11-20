"""원본 보관 정책과 반입 스테이징을 더한다.

`retain_original`은 되돌릴 수 없는 선택을 담는다. false이면 준비가 끝난 뒤 원본이 어디에도 남지 않는다.
그래서 JSONB settings가 아니라 컬럼으로 둔다. 조회·집계·제약이 필요하고, 이 값의 뜻을 스키마만 봐도 알 수 있어야 한다.

기본값은 false다. 원본에 개인정보가 들어 있으므로 안전한 쪽을 기본으로 둔다.
화면은 프로젝트를 만들 때 이 값을 눈에 띄게 표시한다.

`videos.source_retained`는 원본이 아직 스토리지에 있으면 true다. `source_uri`는 지우지 않는다.
`source_uri`는 출처 기록이고 저작권 판정과 감사의 근거다. 파일이 사라져도 출처는 남아야 한다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """프로젝트 원본 보관 정책과 영상별 원본 잔존 여부를 더한다."""
    op.add_column("projects", sa.Column(
        "retain_original", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    # 기존 영상은 원본이 그대로 있으므로 true로 채워야 사실과 맞는다.
    op.add_column("videos", sa.Column(
        "source_retained", sa.Boolean(), nullable=False, server_default=sa.text("true")))


def downgrade() -> None:
    """변경을 되돌린다."""
    op.drop_column("videos", "source_retained")
    op.drop_column("projects", "retain_original")
