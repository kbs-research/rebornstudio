"""clips (video_id, start_seconds, end_seconds) UNIQUE 인덱스를 더한다.

Revision ID: 0019
Revises: 0018

두 워커가 같은 영상을 동시에 처리하면(bulk_check_summaries) clips가 중복으로 INSERT될 수 있다.
advisory lock과 SKIP LOCKED가 먼저 막고, 남은 경쟁 구간은 이 UNIQUE 인덱스가 막는다.
중복 INSERT는 IntegrityError가 되고 savepoint를 롤백한다.

부분 인덱스 조건은 `video_id IS NOT NULL`이다(클립 단위 작업만).
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """clips에 (video_id, start_seconds, end_seconds) UNIQUE 인덱스를 더한다."""
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_clips_video_time_range "
        "ON clips (video_id, start_seconds, end_seconds)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_clips_video_time_range")
