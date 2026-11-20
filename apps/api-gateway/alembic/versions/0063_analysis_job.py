"""`analysis_job`: 분석 상태를 어댑터 중립 단위로 담는다.

`maiu_job_state`는 영상당 한 행이라 클립마다 분석하면 담을 곳이 없다. 부분 성공을 표현할 수 없고,
재시도가 영상 단위이고, 이름에 벤더가 들어 있다.

옛 테이블은 지우지 않는다. 이 마이그레이션은 새 테이블만 만든다. 쓰는 코드와 읽는 코드를 옮긴 뒤
0064가 백필하고 0093이 옛 테이블을 지운다. 한 번에 옮기면 두 테이블이 어긋났을 때 되돌릴 곳이 없다.

`NULLS NOT DISTINCT`(PG15 이상)가 필요하다. Postgres는 NULL을 서로 다른 값으로 보므로
이 옵션이 없으면 `clip_id IS NULL`인 영상 단위 행이 얼마든지 중복된다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_job",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 분석 단위를 정하는 필드. NULL이면 영상 단위, 값이 있으면 클립 단위다.
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("adapter_key", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        # 어댑터 고유의 단계. 진단에만 쓰고 판정은 `status`만 읽는다.
        sa.Column("stage", sa.String(length=40), nullable=True),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deferred", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("in_flight_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.video_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clip_id"], ["clips.clip_id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('pending','running','done','partial','failed')",
            name="ck_analysis_job_status",
        ),
    )
    # 주의: `NULLS NOT DISTINCT`가 없으면 영상 단위 행(`clip_id IS NULL`)이 중복된다.
    op.execute(
        "ALTER TABLE analysis_job ADD CONSTRAINT uq_analysis_job_unit "
        "UNIQUE NULLS NOT DISTINCT (video_id, clip_id, adapter_key)"
    )
    op.create_index("ix_analysis_job_pick", "analysis_job", ["status", "created_at"])
    op.create_index("ix_analysis_job_project", "analysis_job", ["project_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_analysis_job_project", table_name="analysis_job")
    op.drop_index("ix_analysis_job_pick", table_name="analysis_job")
    op.drop_table("analysis_job")
