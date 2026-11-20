"""videos.upload_status에 'transcode_failed'를 더한다(변환 재시도의 dead-letter).

Revision ID: 0018
Revises: 0017

변환 재시도가 끝없이 반복되지 않도록 3회 실패한 영상을 `upload_status='transcode_failed'`로 옮긴다.
list_pending은 `upload_status='uploaded'`만 고르므로 그 영상은 자동으로 빠진다.
그래서 새 테이블이나 컬럼 없이 dead-letter를 만든다.

이 마이그레이션은 CHECK 제약 `ck_video_upload_status`에 'transcode_failed'를 더한다.
허용 값만 넓히므로 기존 데이터에 영향이 없다. 0050과 0051이 이 값을 다시 뺀다.
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """upload_status 허용 값에 'transcode_failed'를 더한다."""
    op.execute("ALTER TABLE videos DROP CONSTRAINT IF EXISTS ck_video_upload_status")
    op.execute(
        "ALTER TABLE videos ADD CONSTRAINT ck_video_upload_status "
        "CHECK (upload_status IN ('pending','uploaded','failed','transcode_failed'))"
    )


def downgrade() -> None:
    """transcode_failed 행을 'failed'로 바꾼 뒤 제약을 좁힌다."""
    op.execute(
        "UPDATE videos SET upload_status='failed' WHERE upload_status='transcode_failed'"
    )
    op.execute("ALTER TABLE videos DROP CONSTRAINT IF EXISTS ck_video_upload_status")
    op.execute(
        "ALTER TABLE videos ADD CONSTRAINT ck_video_upload_status "
        "CHECK (upload_status IN ('pending','uploaded','failed'))"
    )
