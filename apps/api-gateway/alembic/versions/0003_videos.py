"""videos 테이블. 프로젝트별 영상 메타데이터와 copyright_policy.

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column("video_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_uri", sa.Text),                     # S3 키. presigned 업로드가 끝난 뒤 채운다.
        sa.Column("duration_seconds", sa.Float),
        sa.Column("file_size_bytes", sa.BigInteger),
        sa.Column("copyright_policy", sa.String(30), nullable=False),
        sa.Column("copyright_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("upload_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("broadcaster", sa.String(100)),
        sa.Column("program_name", sa.String(200)),
        sa.Column("episode", sa.String(50)),
        sa.Column("aired_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "copyright_policy IN ('public','restricted','pre_cleared','rights_review_required')",
            name="ck_video_copyright_policy",
        ),
        sa.CheckConstraint(
            "copyright_status IN ('pending','cleared','rejected','quarantine')",
            name="ck_video_copyright_status",
        ),
        sa.CheckConstraint(
            "upload_status IN ('pending','uploaded','failed')",
            name="ck_video_upload_status",
        ),
    )
    op.create_index("idx_videos_project_created", "videos", ["project_id", "created_at"])
    op.create_index(
        "idx_videos_rights_review", "videos", ["project_id"],
        postgresql_where=sa.text(
            "copyright_policy = 'rights_review_required' AND copyright_status = 'pending'"
        ),
    )


def downgrade() -> None:
    op.drop_table("videos")
