"""upload_status='transcode_failed' 행을 현재 쓰는 신호로 옮긴다.

변환 실패는 `transcode_status='failed'`로 나타내고, `transcode_failed` 값을 만드는 코드는 없다.
0051이 이 값을 CHECK에서 빼려면 데이터가 먼저 비어야 한다. expand-contract에서 contract는 데이터가 없을 때만 안전하다.
CHECK부터 좁히면 기존 행이 제약을 위반한 채 남아 그 프로젝트의 영상 조회가 모두 깨진다.

하는 일: `upload_status='transcode_failed'`인 행을 `upload_status='uploaded'`와 `transcode_status='failed'`로 바꾼다.
업로드는 끝났고 막힌 것은 변환이다. `transcode_failed` 값은 두 사실을 한 컬럼에 합쳐 업로드가 실패한 것처럼 읽힌다.
옮긴 뒤에는 화면(`blockOf`)이 변환 실패로 분류한다.
`transcode_status`에 이미 값이 있으면 덮어쓰지 않는다. 그 값이 더 최근의 판정이다.

되돌리기: downgrade는 하지 않는다. 쓰지 않는 값을 다시 만드는 일이고, 어느 행이 원래 그 값이었는지도
알 수 없다. 되돌리기용 표식도 남기지 않는다.
"""
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """쓰지 않는 값을 현재 신호로 옮긴다. CHECK는 다음 마이그레이션(0051)에서 좁힌다."""
    op.execute("""
        UPDATE videos
           SET upload_status = 'uploaded',
               transcode_status = CASE
                   WHEN transcode_status IS NULL OR transcode_status = 'skipped'
                       THEN 'failed'
                   ELSE transcode_status
               END
         WHERE upload_status = 'transcode_failed'
    """)


def downgrade() -> None:
    """되돌리지 않는다. 쓰지 않는 값을 되살리는 일이라 의도적으로 아무것도 하지 않는다."""
