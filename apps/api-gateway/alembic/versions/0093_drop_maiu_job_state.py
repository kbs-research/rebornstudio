"""쓰는 코드가 없는 `maiu_job_state` 테이블을 지운다.

## 무엇이 바뀌나

MI 분석 상태는 `analysis_job`에 쓴다. `maiu_job_state`는 영상당 한 행이라 클립 단위 상태를 담지 못해
0063·0064에서 `analysis_job`으로 옮겼고, 그 뒤로 앱·워커·스크립트가 읽지도 쓰지도 않는다.
테이블과 인덱스 세 개, CHECK 제약, RLS 기본 정책(`maiu_job_state_baseline`, 0017)을 함께 지운다.

## 이행

남은 행은 0064가 `analysis_job`으로 백필한 원본이다. 백필 뒤에는 이 테이블에 새로 쓰는 코드가 없으므로
지워도 분석 상태가 사라지지 않는다. 이 테이블을 읽는 코드가 없는 버전이 배포된 뒤에 적용한다.

## 되돌리기

0092 시점의 빈 테이블을 다시 만든다. 컬럼·기본값·CHECK 제약·인덱스·RLS 정책을 그때와 같게 둔다.
지운 행은 돌아오지 않는다. 같은 내용은 `analysis_job`에 남아 있다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None

_TABLE = "maiu_job_state"


def upgrade() -> None:
    """RLS 정책과 테이블을 지운다. 인덱스와 제약은 테이블과 함께 지워진다."""
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_baseline ON {_TABLE}")
    op.drop_table(_TABLE)


def downgrade() -> None:
    """0092 시점의 빈 `maiu_job_state`를 인덱스·제약·RLS 정책과 함께 다시 만든다."""
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("video_id", UUID(as_uuid=True),
                  sa.ForeignKey("videos.video_id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        sa.Column("maiu_asset_id", sa.Text),
        sa.Column("maiu_analysis_id", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("deferred", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("in_flight_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_msg", sa.Text),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("trace_id", UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending','registering','registered','analyzing','analyzed',"
            "'summarizing','done','failed')",
            name="ck_maiu_status",
        ),
    )
    op.create_index(
        "idx_maiu_active", _TABLE, ["updated_at", "status"],
        postgresql_where=sa.text("status IN ('registering','analyzing','summarizing')"),
    )
    op.create_index("idx_maiu_project", _TABLE, ["project_id", "status"])
    op.create_index("idx_maiu_video", _TABLE, ["video_id"], unique=True)
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_TABLE}_baseline ON {_TABLE} "
        f"AS PERMISSIVE FOR ALL USING (true) WITH CHECK (true)"
    )
