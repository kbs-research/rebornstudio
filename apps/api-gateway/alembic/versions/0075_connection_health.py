"""자격증명 연결의 마지막 점검 결과 `connections.last_checked_*`를 더한다.

컬럼으로 두는 이유: 플랫폼 진단은 대상이 하나라 Redis 키 하나로 충분하다. 연결은 행마다 상태가 다르고
목록 화면이 행마다 보여 줘야 한다. 그러면 조회가 곧 조인이므로 값이 행에 붙는 편이 맞다.
또 Redis는 사라져도 되는 캐시로 쓰는데, 이 자격증명을 마지막으로 언제 확인했는지는 사라지면 안 되는 사실이다.
다시 확인하기 전까지 알 방법이 없기 때문이다.

이력 테이블로 두지 않는 이유: 이 컬럼이 담는 값은 지금 이 연결이 정상인지 하나뿐이다.
언제부터 실패했는지는 `domain_events`(`connection.health_changed`)가 상태가 바뀐 순간만 남겨 확인할 수 있다.
점검마다 행을 쌓으면 15분마다 연결 수만큼 늘어나는데 그중 쓸모 있는 것은 상태 전이뿐이다.

`NULL`은 아직 점검하지 않았다는 뜻이다: `last_status`가 `NULL`이면 한 번도 점검한 적이 없다.
화면은 이 값을 정상(초록)으로 그리지 않는다(없는 값을 지어내지 않는다). 기존 행은 모두 `NULL`에서 시작한다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("connections", sa.Column("last_checked_at", sa.DateTime(timezone=True)))
    op.add_column("connections", sa.Column("last_status", sa.String(10)))
    op.add_column("connections", sa.Column("last_detail", sa.Text()))
    op.create_check_constraint(
        "ck_connection_last_status",
        "connections",
        "last_status IS NULL OR last_status IN ('ok','warn','fail')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_connection_last_status", "connections", type_="check")
    op.drop_column("connections", "last_detail")
    op.drop_column("connections", "last_status")
    op.drop_column("connections", "last_checked_at")
