"""버려진 채 열려 있는 단계 런을 닫는다.

리스가 만료돼 회수된 뒤 재투입되면 앞 시도의 행이 `running`으로 남는다. `finish`가 최신 행 하나만 닫기 때문이다.
그러면 화면이 이미 끝난 시도를 진행 중으로 표시하고, 끝나지 않는 행이 소요 통계(p95)에 섞인다.

판정 기준: 같은 (영상, 단계)에 더 나중 행이 있으면 그 앞 행은 버려진 시도다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """뒤에 다른 시도가 있는 running 행을 failed로 닫는다."""
    # 끝난 시각은 다음 시도가 시작된 시각이다. 앞 시도는 그 시각에 버려졌다.
    # `now()`로 닫으면 소요 시간이 마이그레이션 시점까지 늘어나 통계(p95)가 틀린다.
    op.execute(sa.text("""
        UPDATE media_stage_runs r
        SET status = 'failed',
            finished_at = (
                SELECT r2.started_at FROM media_stage_runs r2
                WHERE r2.video_id = r.video_id AND r2.stage = r.stage AND r2.id > r.id
                ORDER BY r2.id LIMIT 1
            ),
            error = COALESCE(r.error, '회수됨 — 새 시도가 시작되어 버려짐')
        WHERE r.status = 'running'
          AND EXISTS (
            SELECT 1 FROM media_stage_runs r2
            WHERE r2.video_id = r.video_id AND r2.stage = r.stage AND r2.id > r.id
          )
    """))


def downgrade() -> None:
    """되돌리지 않는다. 어느 행이 원래 running이었는지 알 수 없다."""
