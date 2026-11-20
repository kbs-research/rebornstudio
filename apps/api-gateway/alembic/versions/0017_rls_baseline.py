"""핵심 multi-tenant 테이블에 RLS를 켜고 기본 정책을 둔다.

Revision ID: 0017
Revises: 0016

권한 검사는 애플리케이션 계층에 있다. DB 계층의 RLS는 다른 조직의 행을 실수로 조회하는 것을
한 번 더 막는 심층 방어다. 이 마이그레이션은 RLS 기반만 준비한다.

1. 핵심 테이블 7개에 ENABLE ROW LEVEL SECURITY를 건다.
2. 모든 행을 허용하는 기본 PERMISSIVE 정책 `USING (true)`를 둔다. 동작은 바뀌지 않는다.
3. 테이블 소유자는 PostgreSQL 기본값으로 RLS를 우회한다.

정책을 좁히려면 세 가지가 더 필요하다.

- 애플리케이션 연결 역할을 admin_role과 app_role로 나눈다.
- 미들웨어가 요청마다 `SET LOCAL app.user_id = '<uuid>'`를 실행한다.
- 정책을 `USING (project_id IN (SELECT project_id FROM project_members ...))`처럼 좁힌다.

downgrade는 RLS를 끄고 정책도 지운다.
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# 핵심 multi-tenant 테이블(조직·프로젝트 경계가 있는 테이블)
_TABLES = (
    "projects",
    "videos",
    "work_items",
    "clips",
    "labels",
    "reviews",
    "maiu_job_state",
)


def upgrade() -> None:
    """테이블 7개에 RLS를 켜고 모든 행을 허용하는 기본 정책을 둔다."""
    for t in _TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        # 기본 정책은 모든 행을 허용한다.
        op.execute(
            f"CREATE POLICY {t}_baseline ON {t} "
            f"AS PERMISSIVE FOR ALL USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    """RLS를 끄고 정책을 지운다."""
    for t in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {t}_baseline ON {t}")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
