"""project_members.role에 운영자 계층 'operator'를 더한다.

관리자와 작업자 사이의 운영 권한 계층이다. 값만 더하므로 기존 역할은 그대로다.
Postgres는 CHECK 제약을 고칠 수 없어서 지우고 다시 만든다.

Revision ID: 0029
Revises: 0028
"""
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_OLD = (
    "role IN ('project_admin','reviewer','annotator','viewer',"
    "'rights_reviewer','legal_reviewer')"
)
_NEW = (
    "role IN ('project_admin','operator','reviewer','annotator','viewer',"
    "'rights_reviewer','legal_reviewer')"
)


def upgrade() -> None:
    op.drop_constraint("ck_member_role", "project_members", type_="check")
    op.create_check_constraint("ck_member_role", "project_members", _NEW)


def downgrade() -> None:
    # 제약을 되돌리기 전에 operator 멤버를 viewer로 내린다. 그러지 않으면 제약 생성이 실패한다.
    op.execute("UPDATE project_members SET role='viewer' WHERE role='operator'")
    op.drop_constraint("ck_member_role", "project_members", type_="check")
    op.create_check_constraint("ck_member_role", "project_members", _OLD)
