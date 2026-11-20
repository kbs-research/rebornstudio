"""공개 가입·가입 승인·OAuth를 없앤다. 계정 상태를 활성·정지 둘로 줄이고 OAuth 컬럼을 지운다.

## 무엇이 바뀌나

RebornStudio는 최고관리자만 계정을 만든다. 공개 가입과 가입 승인, 소셜 로그인이 없다.
그래서 계정 상태 `pending`(승인 대기)·`rejected`(거절)와 `users.oauth_provider`·`oauth_subject`를 지운다.
상태 컬럼의 기본값은 `active`가 된다.

## 이행

승인 대기나 거절 상태로 남은 계정은 `suspended`(정지)로 바꾼다. 로그인할 수 없다는 점은 그대로다.
최고관리자가 필요한 계정만 다시 활성으로 바꾼다.

## 되돌리기

OAuth 컬럼과 고유 제약을 빈 값으로 다시 만들고 상태 제약과 기본값을 되돌린다. 정지로 바꾼 계정은 그대로 둔다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """승인 대기·거절 계정을 정지로 바꾸고 상태 제약과 OAuth 컬럼을 줄인다."""
    op.execute(
        "UPDATE users SET approval_status = 'suspended' "
        "WHERE approval_status IN ('pending','rejected')"
    )
    op.drop_constraint("ck_user_approval_status", "users", type_="check")
    op.create_check_constraint(
        "ck_user_approval_status", "users", "approval_status IN ('active','suspended')")
    op.alter_column("users", "approval_status", server_default="active")
    op.drop_constraint("uq_user_oauth", "users", type_="unique")
    op.drop_column("users", "oauth_subject")
    op.drop_column("users", "oauth_provider")


def downgrade() -> None:
    """OAuth 컬럼과 고유 제약을 되살리고 상태 제약과 기본값을 되돌린다."""
    op.add_column("users", sa.Column("oauth_provider", sa.String(20)))
    op.add_column("users", sa.Column("oauth_subject", sa.String(255)))
    op.create_unique_constraint("uq_user_oauth", "users", ["oauth_provider", "oauth_subject"])
    op.alter_column("users", "approval_status", server_default="pending")
    op.drop_constraint("ck_user_approval_status", "users", type_="check")
    op.create_check_constraint(
        "ck_user_approval_status", "users",
        "approval_status IN ('pending','active','suspended','rejected')")
