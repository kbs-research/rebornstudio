"""원본 체크섬을 더한다. 고객이 가진 파일이 분석한 파일과 같은지 증명한다.

납품 패키지에 미디어를 싣지 않으므로 클립은 고객이 가진 원본의 구간을 타임코드로 가리킨다.
고객의 원본이 분석한 파일과 다르면 모든 시각이 어긋나는데, 같은 파일인지 확인할 방법이 없으면 원인을 가릴 수 없다.

`sha256`을 쓴다. 고객이 `sha256sum`으로 그대로 재현할 수 있어야 한다.
S3 ETag는 멀티파트 업로드에서 파트 크기에 따라 달라지므로 받는 쪽이 재현할 수 없다.
"""
from alembic import op
import sqlalchemy as sa

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("source_sha256", sa.Text(), nullable=True))
    # 체크섬을 아직 계산하지 않은 행을 고르는 beat 쿼리용. 값이 있는 행은 볼 필요가 없다.
    op.create_index(
        "idx_videos_source_sha256_missing",
        "videos",
        ["video_id"],
        postgresql_where=sa.text("source_sha256 IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_videos_source_sha256_missing", table_name="videos")
    op.drop_column("videos", "source_sha256")
