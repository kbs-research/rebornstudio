"""문장 타임코드 스위치를 더하고 배정 만료 기본값을 24시간으로 바꾼다.

타임코드: 구간은 선택 항목이라 제출을 막지 않는다. 타임코드를 쓰지 않는 프로젝트는 스위치를 꺼서
레인과 '따라 찍기'를 숨긴다. 기본값은 기존 동작과 같은 true다.

만료 24시간: 반려 알림을 보고 다음 날 재작업하는 경우 4시간이면 그 사이 자동 반납되어 초안까지 지워진 채
공용 풀로 돌아간다. 그러면 라벨러가 반려 알림을 받은 작업이 자기 목록에 없다.

기존 프로젝트도 4에서 24로 옮긴다. 4는 이전 기본값이라 그대로 두면 고르지 않은 값이 4로 정한 값처럼 남는다.
다른 값으로 바꾼 프로젝트는 건드리지 않는다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column(
        "sentence_timecode_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.alter_column("projects", "default_assignment_ttl_hours", server_default="24")
    op.execute("UPDATE projects SET default_assignment_ttl_hours = 24 "
               "WHERE default_assignment_ttl_hours = 4")


def downgrade() -> None:
    op.alter_column("projects", "default_assignment_ttl_hours", server_default="4")
    op.drop_column("projects", "sentence_timecode_enabled")
