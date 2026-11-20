"""선택 항목 수집·이용 동의 시각(`users.optional_privacy_consented_at`)을 더한다.

바뀌는 것: 개인정보 수집·이용 동의를 필수와 선택으로 나눈다. 필수 동의는 `privacy_consented_at`에 그대로 두고,
선택 항목(전화번호·생년월일·주소)의 동의는 이 컬럼에 둔다. 선택 항목은 이 시각이 있을 때만 저장하고,
선택 동의를 철회하면 선택 항목을 지우고 이 시각을 비운다(`services/auth/consent.py`).

이행: 나누기 전에는 동의 하나가 필수 항목과 입력한 선택 항목을 함께 덮었다. 그래서 선택 항목 값이 하나라도
있는 사용자는 그 동의 시각을 선택 동의 시각으로 옮긴다. 값이 없는 사용자는 비워 둔다.
필수 동의 시각이 없는데 선택 항목 값이 있는 행은 옮길 동의가 없어 비워 둔다. 값은 지우지 않는다.

되돌리기: 컬럼을 지운다. 선택 항목 값은 그대로 남고, 동의 하나가 모든 항목을 덮던 이전 상태가 된다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None

#: 선택 동의 시각을 옮겨 받는 사용자. 필수 동의가 있고 선택 항목 값이 하나라도 있는 행이다.
BACKFILL_SQL = """
UPDATE users
   SET optional_privacy_consented_at = privacy_consented_at
 WHERE privacy_consented_at IS NOT NULL
   AND (phone_number IS NOT NULL OR birth_date IS NOT NULL OR address IS NOT NULL)
"""


def upgrade() -> None:
    """선택 동의 시각 컬럼을 더하고 선택 항목 값이 있는 사용자의 동의 시각을 옮긴다."""
    op.add_column(
        "users", sa.Column("optional_privacy_consented_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    """선택 동의 시각 컬럼을 지운다."""
    op.drop_column("users", "optional_privacy_consented_at")
