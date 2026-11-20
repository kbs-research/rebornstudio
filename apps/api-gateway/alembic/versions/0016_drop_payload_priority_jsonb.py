"""work_items.payload['priority'] JSONB에 남은 키를 지운다.

Revision ID: 0016
Revises: 0015

priority는 0015가 INT 컬럼으로 옮겼고 JSONB 키를 읽는 코드는 없다.
멱등이다. payload에 'priority' 키가 있는 행만 바꾸고 다른 키는 그대로 둔다.
downgrade는 컬럼 값을 다시 JSONB로 복사한다.
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE work_items
        SET payload = payload - 'priority'
        WHERE payload ? 'priority'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE work_items
        SET payload = payload || jsonb_build_object('priority', priority)
    """)
