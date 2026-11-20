"""notifications.link: 알림을 누르면 이동할 앱 안 경로(딥링크).

반려(rework) 알림은 작업자를 해당 재작업 라벨링(`/work/{id}/label`)으로 바로 보낸다.
기존 알림과 링크 없는 알림은 NULL이고, 눌러도 이동하지 않는다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """notifications에 nullable link 컬럼을 더한다."""
    op.add_column("notifications", sa.Column("link", sa.String(300), nullable=True))


def downgrade() -> None:
    """link 컬럼을 지운다."""
    op.drop_column("notifications", "link")
