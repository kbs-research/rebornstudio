"""캡션 품질검수 실행의 검사 시점 제약에 `recheck`(다시 검사)를 더한다.

관리자가 보류 화면에서 누른 `다시 검사`를 승인 실행과 다른 값으로 남긴다.
`approve`로 남기면 승인이 없던 시각에 `승인 시` 실행이 있는 것처럼 실행 이력에 보이고,
`검사 시점: 승인 시` 필터와 감사 기록의 payload에도 섞인다.

이 마이그레이션은 CHECK 제약 `ck_qc_run_trigger`만 넓힌다.
기존 행은 바꾸지 않는다. 이 마이그레이션 이전의 `다시 검사` 실행은 `approve`로 남아 있어 실제 승인 실행과
행만 보고 구분할 수 없다(같은 초의 `caption_qc.rechecked` 감사 기록으로만 추정할 수 있다).

되돌리기: `recheck` 행이 있으면 제약을 좁힐 수 없다. 그 행을 `approve`로 바꾼 뒤 제약을 되돌린다.
게이트와 납품 판정은 `approve`와 `recheck`를 같게 보므로 바꿔도 납품 결과는 그대로다.
"""
from __future__ import annotations

from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """검사 시점 제약에 `recheck`를 더한다."""
    op.drop_constraint("ck_qc_run_trigger", "caption_qc_runs", type_="check")
    op.create_check_constraint(
        "ck_qc_run_trigger", "caption_qc_runs", "trigger IN ('submit','approve','recheck')"
    )


def downgrade() -> None:
    """`recheck` 행을 `approve`로 되돌린 뒤 제약을 좁힌다."""
    op.execute("UPDATE caption_qc_runs SET trigger = 'approve' WHERE trigger = 'recheck'")
    op.drop_constraint("ck_qc_run_trigger", "caption_qc_runs", type_="check")
    op.create_check_constraint(
        "ck_qc_run_trigger", "caption_qc_runs", "trigger IN ('submit','approve')"
    )
