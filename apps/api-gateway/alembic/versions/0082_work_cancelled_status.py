"""작업의 종료 상태 `cancelled`를 더한다.

바뀌는 것: `work_items.status`의 허용 값에 `cancelled` 하나를 더한다. 데이터는 바꾸지 않는다.
값을 쓰는 것은 앱 코드이고, 이 마이그레이션은 그 값을 쓸 수 있게만 한다.

두 경우가 같은 상태를 쓴다.

- 저작권으로 제외된 클립의 진행 중 작업을 종료한다. 종료하지 않으면 그 작업이 라벨링·검수를 거쳐 완료 화면까지
  간 뒤 납품에서만 빠지고, 그 사이 사람의 작업 시간이 든다.
- 영상을 삭제(보관)하면 그 영상의 미완료 작업을 닫는다. 닫지 않으면 큐에 남아 라벨러가 지운 영상의 클립을
  가져가고, 집계의 모집단이 화면마다 달라진다.

`available`로 되돌리면 다시 가져갈 수 있고, `rejected`는 사람의 반려 판정이라 뜻이 다르다.
그래서 종료를 나타내는 값을 새로 만든다.

행을 지우지 않는 이유는 두 가지다.

1. `submitted`·`rejected`까지 간 작업에는 사람이 쓴 시간이 들어 있다. 지우면 누가 무엇을 했는지가 감사 기록 한 줄로만
   남는다.
2. 지운 행이 집계를 부풀리는 문제는 모집단에서 빼는 것으로도 해결된다.
   `analytics.stats._HUMAN_WORK`가 `cancelled`를 제외하므로 진척률 분모·큐 깊이·시간 집계에 들어가지 않는다.

되돌리기: `downgrade`는 제약을 이전 목록으로 되돌리기 전에 남아 있는 `cancelled` 행을 `available`로 바꾼다.
그러지 않으면 데이터에 이미 있는 값 때문에 제약 생성이 실패한다.
되돌린 행은 큐에 다시 나타난다. 이 마이그레이션 이전의 동작과 같다.
"""
from __future__ import annotations

from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None

_OLD = "status IN ('available','claimed','submitted','approved','rejected','quarantine')"
_NEW = (
    "status IN ('available','claimed','submitted','approved','rejected',"
    "'quarantine','cancelled')"
)


def upgrade() -> None:
    op.drop_constraint("ck_work_status", "work_items", type_="check")
    op.create_check_constraint("ck_work_status", "work_items", _NEW)


def downgrade() -> None:
    # 제약을 좁히기 전에 `cancelled` 행을 먼저 바꾼다. 순서를 바꾸면 제약 생성이 실패한다.
    op.execute("UPDATE work_items SET status = 'available' WHERE status = 'cancelled'")
    op.drop_constraint("ck_work_status", "work_items", type_="check")
    op.create_check_constraint("ck_work_status", "work_items", _OLD)
