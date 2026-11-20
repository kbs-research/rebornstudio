"""저작권 목록 집계용 인덱스 clips(project_id, video_id, rights_status)를 더한다.

저작권 2단 목록은 영상별 미판정 수와 전체 수(진행률의 분모)를 함께 센다.
미판정만 세면 `WHERE rights_status IN (...)` 조건으로 부분 인덱스(`idx_clips_project_pending`)를 일부라도 쓴다.
전체를 세려면 그 조건을 WHERE에서 빼야 하고, 남는 조건은 `WHERE project_id = ? GROUP BY video_id`다.
`clips`에는 project_id 단독 인덱스가 없어서 그대로 두면 목록을 열 때마다 프로젝트의 클립 전체를 훑는다.
클립 수는 영상 시간에 비례하므로 영상이 늘수록 비용이 커진다.

세 컬럼인 이유: `rights_status`까지 넣어야 `count(*) FILTER (WHERE rights_status IN ...)`가 힙을 다시 읽지 않는다.
`(project_id, video_id)`만 두면 그룹핑은 인덱스로 하지만 FILTER 판정에서 행마다 힙을 읽는다.

컬럼 순서: `project_id`는 등호 조건, `video_id`는 그룹 키, `rights_status`는 값이다.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_clips_project_video_rights",
        "clips",
        ["project_id", "video_id", "rights_status"],
    )


def downgrade() -> None:
    op.drop_index("idx_clips_project_video_rights", table_name="clips")
