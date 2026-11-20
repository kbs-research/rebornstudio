"""영상 저작권 상태를 `cleared` 하나로 줄인다.

RebornStudio는 저작권 검수 단계가 없어 영상은 늘 허가 상태다. 판정 대기(`pending`)와 제외(`rejected`)를
만들 경로가 없으므로 남은 행을 `cleared`로 바꾸고 상태 제약을 줄인다. 클립 단위 상태(`clips.rights_status`)는
비식별화 신고 처리가 쓰므로 그대로 둔다. 컬럼은 RebornStudio Pro와 같은 스키마를 유지하려고 남긴다.

## 되돌리기

제약만 이전 목록으로 되돌린다. 바꾼 상태는 돌아오지 않는다.
"""
from alembic import op

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """남은 영상을 허가 상태로 바꾸고 상태 제약을 `cleared` 하나로 줄인다."""
    op.execute("UPDATE videos SET copyright_status = 'cleared' WHERE copyright_status <> 'cleared'")
    op.drop_constraint("ck_video_copyright_status", "videos", type_="check")
    op.create_check_constraint(
        "ck_video_copyright_status", "videos", "copyright_status IN ('cleared')")


def downgrade() -> None:
    """상태 제약을 이전 목록으로 되돌린다."""
    op.drop_constraint("ck_video_copyright_status", "videos", type_="check")
    op.create_check_constraint(
        "ck_video_copyright_status", "videos",
        "copyright_status IN ('pending','cleared','rejected')")
