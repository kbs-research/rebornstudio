"""maiu_jobs를 maiu_job_state로 바꾼다(8개 상태, deferred, retry_count).

Revision ID: 0012
Revises: 0011

maiu_jobs의 4개 상태로는 reconciler의 5단계 수명 주기를 표현할 수 없어 테이블을 새로 만든다.
MAIU API가 ID를 두 단계로 발급하므로 maiu_asset_id와 maiu_analysis_id를 나눈다.

- maiu_jobs를 지운다. 데이터가 없는 placeholder 테이블이다.
- maiu_job_state를 만든다(BIGSERIAL id, 8개 상태, deferred, retry·in_flight 횟수, trace_id).
- 인덱스 3개: idx_maiu_active(registering·analyzing·summarizing), idx_maiu_project, idx_maiu_video(UNIQUE).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("maiu_jobs")  # 데이터가 없는 placeholder 테이블

    op.create_table(
        "maiu_job_state",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("video_id", UUID(as_uuid=True),
                  sa.ForeignKey("videos.video_id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        # MAIU 외부 ID. Naver Cloud가 `register`와 `analyze` 두 단계에서 하나씩 발급한다.
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
    # 진행 중 조회·프로젝트별 조회·영상별 유일성 인덱스
    op.create_index(
        "idx_maiu_active", "maiu_job_state", ["updated_at", "status"],
        postgresql_where=sa.text("status IN ('registering','analyzing','summarizing')"),
    )
    op.create_index("idx_maiu_project", "maiu_job_state", ["project_id", "status"])
    op.create_index("idx_maiu_video", "maiu_job_state", ["video_id"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_maiu_video", table_name="maiu_job_state")
    op.drop_index("idx_maiu_project", table_name="maiu_job_state")
    op.drop_index("idx_maiu_active", table_name="maiu_job_state")
    op.drop_table("maiu_job_state")

    # 역방향은 0007의 스키마를 되살린다.
    op.create_table(
        "maiu_jobs",
        sa.Column("job_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_item_id", UUID(as_uuid=True),
                  sa.ForeignKey("work_items.work_item_id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("external_id", sa.String(100), unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="dispatched"),
        sa.Column("request_payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("response_payload", JSONB),
        sa.Column("error", sa.Text),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('dispatched','completed','failed','cancelled')",
            name="ck_maiu_job_status",
        ),
    )
