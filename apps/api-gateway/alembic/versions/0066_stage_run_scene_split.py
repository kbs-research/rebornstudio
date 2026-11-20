"""단계 이력 CHECK에 `scene_split`을 더한다. 씬 분할 잡이 런 행을 남긴다.

`scene_split`은 처리 단계 목록에 있지만 단계 이력 CHECK에는 없다.
CHECK를 넓히지 않으면 씬 분할 런 행의 INSERT가 제약 위반으로 실패하고, 씬 분할 잡이 시작하지 못한다.
0053에서 `publish`를 더한 것과 같은 경우다.
"""
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

_OLD = ("stage IN ('upload','probe','transcode','publish','register',"
        "'analyze','summarize','deident')")
_NEW = ("stage IN ('upload','probe','transcode','publish','register',"
        "'scene_split','analyze','summarize','deident')")


def upgrade() -> None:
    op.drop_constraint("ck_msr_stage", "media_stage_runs", type_="check")
    op.create_check_constraint("ck_msr_stage", "media_stage_runs", _NEW)


def downgrade() -> None:
    op.execute("DELETE FROM media_stage_runs WHERE stage = 'scene_split'")
    op.drop_constraint("ck_msr_stage", "media_stage_runs", type_="check")
    op.create_check_constraint("ck_msr_stage", "media_stage_runs", _OLD)
