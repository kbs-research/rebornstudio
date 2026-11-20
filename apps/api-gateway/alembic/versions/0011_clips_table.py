"""clips 테이블을 만들고 work_items.clip_id를 더한다.

Revision ID: 0011
Revises: 0010

영상 하나에서 클립 N개와 작업 N개가 나오는 구조의 스키마다. MAIU reconciler가 클립을 만든다.
영상 단위 작업(clip_id IS NULL)도 그대로 쓸 수 있다.

- 새 `clips` 테이블(rights_status 6종 포함)을 만든다.
- `work_items.clip_id`를 nullable로 더한다. 기존 행과 호환된다.
- `work_items`의 유일성을 다시 설계한다.
    기존 `uq_work_item_video`를 지운다.
    부분 유니크 인덱스 `uq_work_item_video_only`를 만든다(clip_id IS NULL).
    유니크 인덱스 `uq_work_item_clip`을 만든다. clip_id가 있을 때만 적용되므로 NULL은 여러 개 허용된다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. clips 테이블
    op.create_table(
        "clips",
        sa.Column("clip_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True),
                  sa.ForeignKey("videos.video_id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        # 시간 범위(영상 안에서 클립의 위치)
        sa.Column("start_seconds", sa.Numeric(10, 3), nullable=False),
        sa.Column("end_seconds", sa.Numeric(10, 3), nullable=False),
        # MAIU 산출물
        sa.Column("auto_caption", JSONB),       # {brief, detailed, ...}
        sa.Column("speakers", JSONB),           # [{name, segments: [...]}, ...]
        # 권리 상태(6종)
        sa.Column("rights_status", sa.String(30), nullable=False, server_default="pending"),
        # MAIU 작업 연결.
        sa.Column("maiu_job_id", UUID(as_uuid=True)),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "rights_status IN ('pending','auto_cleared','cleared','cleared_with_edit','rejected','legal_review')",
            name="ck_clip_rights_status",
        ),
        sa.CheckConstraint(
            "end_seconds > start_seconds", name="ck_clip_time_range",
        ),
    )
    op.create_index("idx_clips_video", "clips", ["video_id", "start_seconds"])
    op.create_index("idx_clips_project_pending", "clips", ["project_id"],
                    postgresql_where=sa.text("rights_status = 'pending'"))

    # 2. work_items에 clip_id를 더한다.
    op.add_column("work_items",
                  sa.Column("clip_id", UUID(as_uuid=True),
                            sa.ForeignKey("clips.clip_id", ondelete="CASCADE")))

    # 3. 유일성을 다시 설계한다.
    op.drop_constraint("uq_work_item_video", "work_items", type_="unique")
    # 영상 단위 작업: clip_id IS NULL일 때만 영상별로 유일하다.
    op.create_index(
        "uq_work_item_video_only", "work_items", ["video_id"],
        unique=True, postgresql_where=sa.text("clip_id IS NULL"),
    )
    # 클립 단위 작업: clip_id가 있으면 클립별로 유일하다.
    op.create_index(
        "uq_work_item_clip", "work_items", ["clip_id"],
        unique=True, postgresql_where=sa.text("clip_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_work_item_clip", table_name="work_items")
    op.drop_index("uq_work_item_video_only", table_name="work_items")
    op.create_unique_constraint("uq_work_item_video", "work_items", ["video_id"])
    op.drop_column("work_items", "clip_id")

    op.drop_index("idx_clips_project_pending", table_name="clips")
    op.drop_index("idx_clips_video", table_name="clips")
    op.drop_table("clips")
