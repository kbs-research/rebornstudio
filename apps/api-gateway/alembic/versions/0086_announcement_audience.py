"""공지에 받는 역할 묶음(`audience`)을 더한다.

프로젝트 공지를 전원·작업자만·관리자만 중에서 골라 올린다.
서버는 고른 역할에게만 공지를 보여 주고 알림을 보낸다.

- `all`: 전원. 기존 공지는 모두 이 값이다.
- `workers`: 작업자(라벨러·검수자·저작권 검수자·법무 검수자).
- `managers`: 관리자(프로젝트 관리자·운영자).

확장만 한다. 기본값이 `all`이라 이 컬럼을 읽지 않는 코드가 쓴 행도 전원 공지로 남는다.
되돌리기: 컬럼과 제약을 지운다. 대상을 좁힌 공지는 전원 공지가 된다.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """`announcements.audience` 컬럼과 값 제약을 더한다."""
    op.add_column(
        "announcements",
        sa.Column("audience", sa.String(length=10), nullable=False, server_default="all"),
    )
    op.create_check_constraint(
        "ck_announcement_audience", "announcements", "audience IN ('all','workers','managers')"
    )


def downgrade() -> None:
    """제약과 컬럼을 지운다."""
    op.drop_constraint("ck_announcement_audience", "announcements", type_="check")
    op.drop_column("announcements", "audience")
