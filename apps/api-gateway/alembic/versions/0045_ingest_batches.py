"""반입 묶음을 독립 레코드로 만든다. ingest_batches를 만들고 기존 batch id를 백필한다.

`videos.upload_batch_id`(0044)는 자기 행이 없는 UUID라 이름·출처·수행자가 없고 목록으로 볼 수 없다.
묶음 행을 두면 반입 직후의 URL을 닫아도 목록에서 그 묶음을 다시 찾을 수 있다.

백필: 이미 붙어 있는 batch id를 모두 행으로 만든다. 시각은 그 묶음의 첫 영상, 수행자는 업로더다.
출처는 과거 데이터로 구분할 수 없어 'upload'로 둔다. 확인할 수 없는 출처를 지어내지 않는다.
FK는 백필한 뒤에 건다. 먼저 걸면 기존 행이 모두 제약을 위반한다.

Revision ID: 0045
Revises: 0044
"""
import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingest_batches",
        sa.Column("batch_id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", sa.UUID(as_uuid=True),
            sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("created_by", sa.UUID(as_uuid=True), sa.ForeignKey("users.user_id")),
        sa.Column("source", sa.String(20), nullable=False, server_default="upload"),
        sa.Column("label", sa.String(200)),
        sa.Column("note", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint("source IN ('upload','ncp')", name="ck_ingest_batch_source"),
    )
    # 목록은 항상 프로젝트 안에서 최신순이므로 복합 인덱스 하나로 한 번에 스캔한다.
    op.create_index(
        "ix_ingest_batches_project_created", "ingest_batches", ["project_id", "created_at"]
    )

    # 기존 batch id를 백필한다. DISTINCT ON으로 묶음별 첫 영상의 시각과 업로더를 고른다.
    op.execute(
        """
        INSERT INTO ingest_batches (batch_id, project_id, created_by, source, created_at)
        SELECT DISTINCT ON (v.upload_batch_id)
               v.upload_batch_id, v.project_id, v.uploaded_by, 'upload', MIN(v.created_at)
                 OVER (PARTITION BY v.upload_batch_id)
        FROM videos v
        WHERE v.upload_batch_id IS NOT NULL
        ORDER BY v.upload_batch_id, v.created_at
        ON CONFLICT (batch_id) DO NOTHING
        """
    )

    # FK는 백필한 뒤에 건다. 먼저 걸면 기존 행이 모두 제약을 위반한다.
    op.create_foreign_key(
        "fk_videos_upload_batch", "videos", "ingest_batches",
        ["upload_batch_id"], ["batch_id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_videos_upload_batch", "videos", type_="foreignkey")
    op.drop_index("ix_ingest_batches_project_created", table_name="ingest_batches")
    op.drop_table("ingest_batches")
