"""내보내기에 선택 항목 제외 옵션(`omit_optional`)을 더한다.

켜면 납품 메타데이터(`metadata/{clip_id}.json`)에서 라벨링 선택 항목 다섯 키를 뺀다.
뺀 키 집합은 `export_package.OPTIONAL_LABEL_FIELDS`가 정한다.
옵션이 행에 남아야 워커가 만들 때 읽고, 이력 행이 어느 패키지가 키를 뺐는지 보여 준다.

확장만 한다. 기존 행은 모두 키를 담아 만들었으므로 기본값 `false`가 사실이고 백필이 필요 없다.
이전 코드는 이 컬럼을 읽지도 쓰지도 않는다.
되돌리기: 컬럼을 지운다. 옵션을 켠 이력 행의 표시만 사라지고 패키지 파일은 그대로다.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """`exports.omit_optional` 컬럼을 더한다."""
    op.add_column(
        "exports",
        sa.Column("omit_optional", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """`exports.omit_optional` 컬럼을 지운다."""
    op.drop_column("exports", "omit_optional")
