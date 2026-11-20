"""`analysis_job`에 펜싱 토큰과 리스를 더한다. DB에 붙지 않는 워커에 작업을 보내려면 필요하다.

씬 분할 같은 분석 작업을 워커로 보내려면 두 가지가 필요하다.

- 토큰: 완료 보고가 해당 시도의 보고인지 가린다. 토큰이 없으면 리스가 만료돼 회수·재투입된 뒤
  옛 워커가 다시 응답해 보낸 결과도 그대로 받는다.
- 리스: 워커가 비정상 종료되면 그 행이 계속 `running`으로 남는다. 만료 시각이 있어야 회수 스윕이 그 행을 다시 투입할 수 있다.

`analysis_job`은 어댑터 중립 테이블이므로 이 둘도 중립이다. 벤더에 묶인 값이 아니라 작업을 밖으로 보내는
프로토콜의 일부다(`transcode`가 Video에 둔 것과 같은 짝).

기존 행은 둘 다 NULL이다. MI는 워커로 보내지 않고 자체 폴링으로 돌므로 NULL이 맞다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analysis_job", sa.Column("token", sa.String(40), nullable=True))
    op.add_column(
        "analysis_job",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    # 회수 스윕이 만료된 running 행만 고르는 경로. 리스가 있는 행은 워커로 보낸 작업뿐이라
    # 전체의 일부이므로 부분 인덱스로 둔다.
    op.create_index(
        "ix_analysis_job_lease",
        "analysis_job",
        ["lease_until"],
        postgresql_where=sa.text("lease_until IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_job_lease", table_name="analysis_job")
    op.drop_column("analysis_job", "lease_until")
    op.drop_column("analysis_job", "token")
