"""반려 누적 격리를 없앤다. 작업 상태 `quarantine`, 격리 시각, 격리 임계값 컬럼을 지운다.

## 무엇이 바뀌나

RebornStudio는 반려가 쌓여도 작업을 격리하지 않는다. 반려된 작업은 원 작업자의 재작업이나 대기열로 돌아가고,
반려가 여러 번 쌓인 미완 작업에는 관리자 확인 표시만 붙는다(`work.service.classify.needs_admin_check`).
그래서 작업 상태의 `quarantine` 값과 `work_items.quarantined_at`, `projects.reject_to_quarantine_threshold`를 지운다.

## 이행

격리 상태로 남은 작업은 대기열(`available`)로 돌린다. 보유자와 기한을 비우고 반려 횟수는 그대로 둔다.
그래서 돌아간 작업에도 관리자 확인 표시가 붙는다. 격리를 읽거나 쓰는 코드가 없는 버전이 배포된 뒤에 적용한다.

## 되돌리기

두 컬럼을 기본값과 함께 다시 만들고 상태 제약에 `quarantine`을 되돌린다. 대기열로 돌린 작업은 격리로 돌아가지 않는다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None

_WITH = ("status IN ('available','claimed','submitted','approved','rejected',"
         "'quarantine','cancelled')")
_WITHOUT = "status IN ('available','claimed','submitted','approved','rejected','cancelled')"


def upgrade() -> None:
    """격리 작업을 대기열로 돌리고 상태 제약과 두 컬럼에서 격리를 지운다."""
    op.execute(
        "UPDATE work_items SET status = 'available', claimed_by = NULL, claimed_at = NULL, "
        "expires_at = NULL WHERE status = 'quarantine'"
    )
    op.drop_constraint("ck_work_status", "work_items", type_="check")
    op.create_check_constraint("ck_work_status", "work_items", _WITHOUT)
    op.drop_column("work_items", "quarantined_at")
    op.drop_column("projects", "reject_to_quarantine_threshold")


def downgrade() -> None:
    """두 컬럼을 다시 만들고 상태 제약에 `quarantine`을 되돌린다."""
    op.add_column("projects", sa.Column("reject_to_quarantine_threshold", sa.Integer,
                                        nullable=False, server_default="3"))
    op.add_column("work_items", sa.Column("quarantined_at", sa.DateTime(timezone=True)))
    op.drop_constraint("ck_work_status", "work_items", type_="check")
    op.create_check_constraint("ck_work_status", "work_items", _WITH)
