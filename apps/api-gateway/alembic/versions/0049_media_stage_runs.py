"""media_stage_runs: 준비 단계의 시도 이력.

expand만 하는 마이그레이션이다. 기존 테이블과 컬럼을 바꾸지 않으므로 롤링 배포 중에도 옛 코드와 호환된다.
기록은 상태가 바뀔 때 덧붙이고, 진행 판정은 계속 기존 상태 컬럼이 맡는다.

Revision ID: 0049
Revises: 0048
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_stage_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "video_id", UUID(as_uuid=True),
            sa.ForeignKey("videos.video_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(10), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("detail", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "stage IN ('upload','probe','transcode','register','analyze','summarize','deident')",
            name="ck_msr_stage",
        ),
        sa.CheckConstraint("status IN ('running','ok','failed')", name="ck_msr_status"),
    )
    op.create_index("ix_msr_video_id", "media_stage_runs", ["video_id", "id"])
    op.create_index("ix_msr_stage_status", "media_stage_runs", ["stage", "status"])


def downgrade() -> None:
    op.drop_table("media_stage_runs")
