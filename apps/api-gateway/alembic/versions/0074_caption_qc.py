"""캡션 품질검수용 `work_items.qc_status`와 `caption_qc_runs`를 더한다.

캡션 품질검수의 판정·납품 게이트·실행 이력을 담을 스키마를 만든다.

`status`에 값을 더하지 않는 이유: `work_items.status`는 사람의 판정을 담는 컬럼이고 대시보드·통계·필터가
모두 이 값을 센다. 여기에 `qc_hold`를 더하면 그 값을 처리하지 않는 모든 집계의 분모가 조용히 틀린다.
그래서 기계의 판정은 별도 컬럼(`qc_status`)에 둔다.

`pending`이 기본값인 이유: `passed`·`held` 둘만 두면 LLM 호출 실패가 곧 보류가 된다.
429·5xx는 데이터 문제가 아니라 인프라 문제다. 실패한 검사는 `pending`에 머물고 재시도 대상이 된다.
기존 행도 모두 `pending`으로 들어온다. 그래서 게이트는 `!= 'held'`로 짠다.
`== 'passed'`로 짜면 이미 승인된 납품분이 이 마이그레이션 하나로 모두 빠진다.

실행 이력을 별도 테이블에 두는 이유: 같은 work_item을 제출 직후와 승인 직후 두 번 검사한다.
한 행에 덮어쓰면 제출본은 깨끗했는데 검수자가 고친 뒤 걸린 경우를 볼 수 없다.
판정 근거(`findings`)와 그때 쓴 모델·프롬프트 해시를 함께 남겨야 나중에 보류 사유를 재구성할 수 있다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column("qc_status", sa.String(20), nullable=False, server_default="pending"),
    )
    op.create_check_constraint(
        "ck_work_qc_status", "work_items", "qc_status IN ('pending','passed','held')"
    )
    # 보류함은 프로젝트별로 조회한다. held인 행은 소수라 held만 담는 부분 인덱스로 충분하다.
    op.create_index(
        "idx_work_qc_held", "work_items", ["project_id"],
        postgresql_where=sa.text("qc_status = 'held'"),
    )

    op.create_table(
        "caption_qc_runs",
        sa.Column("run_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("work_item_id", UUID(as_uuid=True),
                  sa.ForeignKey("work_items.work_item_id", ondelete="CASCADE"), nullable=False),
        # 검사 시점. 같은 건을 제출 직후와 승인 직후 두 번 검사한다.
        sa.Column("trigger", sa.String(20), nullable=False),
        # 판정 3분류(pass·needs_review·reject_suggested). 게이트(`qc_status`)는 이 값에서 파생된다.
        sa.Column("verdict", sa.String(20), nullable=False),
        # [{check, severity, message, sentence_id?}] 목록. 근거 없이 결과만 두면 리뷰어 화면에
        # 보류 사유를 표시할 수 없다.
        sa.Column("findings", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        # 판정 재구성용. 프롬프트를 바꾸면 판정이 달라진다.
        sa.Column("adapter_key", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100)),
        sa.Column("prompt_hash", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("trigger IN ('submit','approve')", name="ck_qc_run_trigger"),
        sa.CheckConstraint(
            "verdict IN ('pass','needs_review','reject_suggested')", name="ck_qc_run_verdict"
        ),
    )
    # 화면은 항상 마지막 결과를 보여 주므로 건마다 최신 결과를 고르는 인덱스를 둔다.
    op.create_index(
        "idx_qc_runs_item", "caption_qc_runs", ["work_item_id", sa.text("created_at DESC")]
    )


def downgrade() -> None:
    op.drop_index("idx_qc_runs_item", table_name="caption_qc_runs")
    op.drop_table("caption_qc_runs")
    op.drop_index("idx_work_qc_held", table_name="work_items")
    op.drop_constraint("ck_work_qc_status", "work_items", type_="check")
    op.drop_column("work_items", "qc_status")
