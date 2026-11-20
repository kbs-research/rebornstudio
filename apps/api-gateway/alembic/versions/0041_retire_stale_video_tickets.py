"""클립이 이미 생긴 영상에 남은 영상 티켓(MAIU 디스패치 표식)을 지운다.

영상 work_item(clip_id IS NULL)은 이 영상에 MAIU 분석이 필요하다는 표식이다.
분석이 끝나 클립이 생긴 뒤에도 표식이 회수되지 않으면 available 상태로 남는다.
남은 표식은 라벨링 큐에 영상 작업으로 나타나고, claim되면 디스패치 대상에서도 빠지며, 큐 집계를 틀리게 만든다.
reconciler는 클립을 만든 직후 `work_service.retire_video_ticket`으로 표식을 회수한다.
이 마이그레이션은 이미 남아 있는 표식을 한꺼번에 정리한다.

되돌릴 수 없다. 해당 영상은 이미 클립 단위로 진행 중이라 삭제한 표식이 다시 필요하지 않다.

Revision ID: 0041
Revises: 0040
"""
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """클립이 있는 영상의 영상 티켓을 claim된 것까지 포함해 지운다."""
    op.execute(
        """
        DELETE FROM work_items w
         WHERE w.clip_id IS NULL
           AND EXISTS (SELECT 1 FROM clips c WHERE c.video_id = w.video_id)
        """
    )


def downgrade() -> None:
    """되돌리지 않는다. 해당 영상은 이미 클립 단위로 진행 중이라 삭제한 표식이 다시 필요하지 않다."""
