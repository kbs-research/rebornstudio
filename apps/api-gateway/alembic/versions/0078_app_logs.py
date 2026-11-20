"""운영 로그 저장소: 화면에서 읽을 수 있는 세 번째 기록 테이블.

새 테이블을 두는 이유: 이 저장소에는 이미 기록 테이블이 둘 있다.

- `domain_events`: 사람의 행위. append-only이고 계약이며 컴플라이언스 대상이다.
- `media_stage_runs`: 기계의 처리 공정. 영상이 어느 단계에서 무엇을 했는지 담는다.

디버그 로그는 둘 중 어디에도 맞지 않는다. 양이 훨씬 많고(요청마다 여러 줄), 며칠이 지나면 쓸모가 줄고,
형식도 언제든 바뀔 수 있다. 감사 테이블에 섞으면 append-only 기록이 디버그 로그에 묻히고, 보존 정책이 다른 두 기록을
하나로 묶게 된다. 그래서 따로 둔다.

담지 않는 것: 본문과 자격증명은 담지 않는다. 로그 한 줄과 그 줄이 난 맥락(요청 id·행위자·경로·상태·소요)만 담는다.

보존: 기한을 둔다. 감사 기록과 달리 오래 둘 이유가 없고 양이 많다. 기본 14일이고
`app_log_retention_days`로 조절한다(감사 기록과 같은 방식의 정리 태스크).

Revision ID: 0078
Revises: 0077
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """운영 로그 테이블과 화면 필터가 쓰는 인덱스를 만든다."""
    op.create_table(
        "app_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("logger", sa.String(120), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        # 요청 맥락. 워커나 기동 과정에서 난 줄은 NULL이고, 0이나 빈 문자열로 채우지 않는다.
        sa.Column("request_id", sa.String(32)),
        sa.Column("actor_user_id", sa.String(36)),
        sa.Column("path", sa.String(300)),
        sa.Column("method", sa.String(10)),
        sa.Column("status_code", sa.Integer),
        sa.Column("elapsed_ms", sa.Float),
        # 예외 트레이스백처럼 긴 내용. 목록에는 싣지 않고 상세에서만 보여 준다.
        sa.Column("detail", JSONB),
    )
    # 화면 기본 정렬(최신순)과 세 필터 축.
    op.create_index("idx_app_logs_at", "app_logs", [sa.text("at DESC")])
    op.create_index("idx_app_logs_level_at", "app_logs", ["level", sa.text("at DESC")])
    op.create_index("idx_app_logs_rid", "app_logs", ["request_id"])


def downgrade() -> None:
    """테이블째 지운다. 로그는 되살리지 않는다."""
    op.drop_index("idx_app_logs_rid", table_name="app_logs")
    op.drop_index("idx_app_logs_level_at", table_name="app_logs")
    op.drop_index("idx_app_logs_at", table_name="app_logs")
    op.drop_table("app_logs")
