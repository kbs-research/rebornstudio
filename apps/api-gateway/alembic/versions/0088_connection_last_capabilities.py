"""연결에 마지막 점검에서 확인한 서버 기능(`last_capabilities`)을 더한다.

비식별화 서버 연결은 점검할 때 서버가 얼굴 좌표 전송(`coordinates_put_url`)을 지원하는지 확인한다.
지원하지 않는 서버에 좌표를 실으면 모든 잡이 422로 거절되므로, 연결을 저장할 때 이 값을 보고 좌표 전송 켜기를 막는다.
점검 결과 문장(`last_detail`)에서 뽑으면 문구를 고칠 때 판정이 바뀌므로 별도 컬럼에 둔다.

확장만 한다. 이전 코드는 이 컬럼을 읽지도 쓰지도 않는다.
되돌리기: 컬럼을 지운다. 다음 점검이 다시 채운다.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """`connections.last_capabilities` 컬럼을 더한다."""
    op.add_column(
        "connections",
        sa.Column("last_capabilities", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    """`connections.last_capabilities` 컬럼을 지운다."""
    op.drop_column("connections", "last_capabilities")
