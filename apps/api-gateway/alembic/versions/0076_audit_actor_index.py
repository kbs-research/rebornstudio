"""감사 로그 행위자 인덱스를 더한다. 사람별 필터가 전체 스캔을 하지 않게 한다.

감사 화면의 필터 다섯 가지 중 넷은 인덱스를 쓴다.
    (project_id, occurred_at), (event_type, occurred_at), (target_kind, target_id), (occurred_at)

`actor_user_id`에만 인덱스가 없다. 이벤트 수는 대략 영상당 14건과 작업당 2건을 더한 값이라
영상이 수천 개면 수십만 건이 되고, 사람별 조회가 매번 전체 스캔이 된다.
사람별 조회는 부당 반려 조사와 이의 제기 처리에 쓰는 경로다.

화면이 항상 최신순으로 정렬하므로 `occurred_at`을 두 번째 열로 둔다. 필터와 정렬을 한 인덱스가 함께 처리한다.

Revision ID: 0076
Revises: 0075
"""
import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """actor_user_id 필터와 최신순 정렬에 함께 쓰는 복합 인덱스를 만든다."""
    op.create_index(
        "idx_events_actor",
        "domain_events",
        ["actor_user_id", sa.text("occurred_at DESC")],
    )


def downgrade() -> None:
    """인덱스만 되돌리고 데이터는 건드리지 않는다."""
    op.drop_index("idx_events_actor", table_name="domain_events")
