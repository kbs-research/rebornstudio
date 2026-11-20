"""domain_events에 kind(audit|integration)를 더해 이벤트를 두 종류로 나눈다.

같은 테이블에 성격이 다른 두 이벤트가 있다. 감사 기록은 사람이 나중에 읽는 기록이고,
통합 이벤트는 다른 도메인이나 분리한 서비스가 구독할 사실이다. 기존 행은 모두 감사 기록이다.
도메인을 분리하려면 그 도메인이 구독할 이벤트 목록이 필요하므로 구분 표식을 둔다.

outbox 규약: integration 이벤트는 상태 변경과 같은 트랜잭션에 쓴다. audit.emit이 commit 직전에 add하는
방식과 같다. 전달은 구독자가 생길 때 beat 폴링으로 한다.

Revision ID: 0048
Revises: 0047
"""
import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "domain_events",
        sa.Column("kind", sa.String(12), nullable=False, server_default="audit"),
    )
    op.create_check_constraint(
        "ck_domain_event_kind", "domain_events", "kind IN ('audit','integration')"
    )
    # 전달 beat가 훑을 대상. integration은 전체의 극히 일부라 integration만 담는 부분 인덱스로 둔다.
    op.create_index(
        "ix_domain_events_integration", "domain_events", ["occurred_at"],
        postgresql_where=sa.text("kind = 'integration'"),
    )


def downgrade() -> None:
    op.drop_index("ix_domain_events_integration", table_name="domain_events")
    op.drop_constraint("ck_domain_event_kind", "domain_events")
    op.drop_column("domain_events", "kind")
