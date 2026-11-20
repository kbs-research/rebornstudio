"""역할을 줄인다. 플랫폼 역할은 최고관리자·일반 사용자, 프로젝트 역할은 관리자·검수자·라벨러다.

## 이행

- 스태프(`staff`)는 일반 사용자가 된다. 만든 프로젝트의 관리자 멤버십은 그대로 남는다.
- 운영자(`operator`)는 관리자(`project_admin`)가 된다. 운영 화면과 관리 화면이 한 역할로 합쳐진다.
- 뷰어·저작권 검수자·법무 검수자 멤버십은 끝낸다(`left`). 맡을 일이 없는 역할이라 다른 역할로 옮기면
  권한이 넓어진다. 필요한 사람은 관리자가 세 역할 중 하나로 다시 초대한다. 끝낸 행은 새 제약을
  지키도록 역할을 라벨러로 적어 두지만 `left`라서 아무 권한이 없다.

## 되돌리기

제약만 되돌린다. 옮긴 역할과 끝낸 멤버십은 돌아오지 않는다.
"""
from alembic import op

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None

_ENDED = "('viewer','rights_reviewer','legal_reviewer')"


def upgrade() -> None:
    """역할 값을 옮기고 두 CHECK 제약을 줄인다."""
    op.execute("UPDATE users SET global_role = 'user' WHERE global_role = 'staff'")
    op.execute("UPDATE project_members SET role = 'project_admin' WHERE role = 'operator'")
    op.execute(
        "UPDATE project_members SET "
        "left_at = CASE WHEN status = 'left' THEN left_at ELSE now() END, "
        "leave_reason = CASE WHEN status = 'left' THEN leave_reason ELSE '역할 정리로 종료' END, "
        f"status = 'left', role = 'annotator' WHERE role IN {_ENDED}"
    )
    op.drop_constraint("ck_user_global_role", "users", type_="check")
    op.create_check_constraint("ck_user_global_role", "users", "global_role IN ('super_admin','user')")
    op.drop_constraint("ck_member_role", "project_members", type_="check")
    op.create_check_constraint(
        "ck_member_role", "project_members", "role IN ('project_admin','reviewer','annotator')")


def downgrade() -> None:
    """두 CHECK 제약을 이전 역할 목록으로 되돌린다."""
    op.drop_constraint("ck_member_role", "project_members", type_="check")
    op.create_check_constraint(
        "ck_member_role", "project_members",
        "role IN ('project_admin','operator','reviewer','annotator','viewer',"
        "'rights_reviewer','legal_reviewer')")
    op.drop_constraint("ck_user_global_role", "users", type_="check")
    op.create_check_constraint(
        "ck_user_global_role", "users", "global_role IN ('super_admin','staff','user')")
