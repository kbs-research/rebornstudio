"""재설정 토큰에 사용 시각(`used_at`)을 더한다.

재설정 링크를 연 사람에게 만료된 링크와 이미 쓴 링크를 구분해 알린다.
쓴 토큰의 행을 지우면 쓴 링크와 없는 링크를 구분할 수 없다.
그래서 쓴 행은 지우지 않고 `used_at`을 적는다. 사용자당 한 행이라 행 수는 늘지 않고, 재발급이 그 행을 덮는다.

확장만 한다. 이 컬럼을 읽지 않는 코드는 쓴 행을 지우므로 값이 비어 있다.
되돌리기: 컬럼을 지운다. 되돌린 코드는 `used_at`을 보지 않으므로, 되돌리기 전에 쓴 링크가 유효 시간 안에 남아 있으면
다시 쓸 수 있다. 그래서 되돌릴 때는 쓴 행을 먼저 지운다(`DELETE FROM password_reset_tokens WHERE used_at IS NOT NULL`).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """`password_reset_tokens.used_at` 컬럼을 더한다."""
    op.add_column(
        "password_reset_tokens",
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """쓴 행을 지우고 컬럼을 지운다. 되돌린 코드가 쓴 링크를 다시 받지 않게 한다."""
    op.execute("DELETE FROM password_reset_tokens WHERE used_at IS NOT NULL")
    op.drop_column("password_reset_tokens", "used_at")
