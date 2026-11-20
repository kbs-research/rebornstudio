"""work_items.priority를 JSONB 키에서 INT 컬럼과 부분 인덱스로 옮긴다.

Revision ID: 0015
Revises: 0014

JSONB 키(`payload->>'priority'`)로 정렬하면 claim_next가 호출마다 형변환해서 큐가 깊을수록 느려진다.
정식 컬럼과 부분 인덱스(`status='available'` 조건)로 O(log N) 조회를 보장한다.

1. `priority INTEGER NOT NULL DEFAULT 0` 컬럼을 더한다.
2. JSONB의 기존 값(`payload->>'priority'`)을 priority 컬럼으로 옮긴다.
3. claim_next의 조회 조건과 정확히 맞는 부분 인덱스를 만든다.
4. payload['priority']는 기존 코드 호환용으로 남긴다. 0016이 지운다.

downgrade는 인덱스와 컬럼을 지운다. payload는 그대로 둔다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    # 기존 JSONB 값을 옮긴다. NULL이거나 숫자가 아니면 0으로 둔다.
    op.execute("""
        UPDATE work_items
        SET priority = COALESCE(
            CAST(NULLIF(payload->>'priority', '') AS INTEGER), 0
        )
        WHERE payload ? 'priority'
    """)
    # claim_next가 자주 쓰는 경로의 부분 인덱스. `status='available'` 조건과 정렬 키가 쿼리와 같다.
    op.create_index(
        "idx_work_items_claim_order",
        "work_items",
        ["project_id", sa.text("priority DESC"), "created_at"],
        postgresql_where=sa.text("status = 'available'"),
    )


def downgrade() -> None:
    op.drop_index("idx_work_items_claim_order", table_name="work_items")
    op.drop_column("work_items", "priority")
