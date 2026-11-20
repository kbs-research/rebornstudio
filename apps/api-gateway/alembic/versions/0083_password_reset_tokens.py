"""관리자가 발급하는 비밀번호 재설정 토큰 테이블을 더한다.

바뀌는 것: `password_reset_tokens` 테이블 하나를 더한다. 기존 테이블과 데이터는 바꾸지 않는다(expand).

메일 발송이 없으므로 비밀번호를 잊으면 관리자가 1회용 링크를 발급한다.

상태 없는 JWT(비밀번호 해시에서 뽑은 nonce 포함)를 재설정 토큰으로 쓰면 새로 발급할 때 앞 토큰을 무효로 만들 수 없다.
같은 비밀번호에서 뽑은 두 토큰은 서로 독립이다. 유효 시간도 토큰 안에 들어 있어 서버가 회수할 수 없다.
그래서 서버가 토큰 행 하나를 저장한다.

사용자당 한 행인 이유: PK가 `user_id`라 한 사람에게 유효한 토큰은 항상 하나다. 재발급하면 행이 덮여 앞 토큰이
무효가 되고, 사용하면 행을 지워 토큰이 무효가 된다.

해시만 저장하는 이유: `token_hash`는 sha256 hex다. 평문을 두면 DB를 읽을 수 있는 사람이 다른 사람의 계정에
로그인할 수 있다. 대신 서버가 발급한 링크를 다시 보여 줄 수 없다. 그래서 화면은 발급 응답에서 한 번만 보여 주고,
다시 보려면 재발급한다.

`issued_by`를 남긴다. 링크를 사람이 전달하므로 누가 전달했는지가 보안 사고 조사의 출발점이다.
발급 이력은 `domain_events`(`user.password_reset_issued`)에도 남는다. 이 테이블은 현재 유효한 토큰 하나만 담는다.

되돌리기: 테이블을 지운다. 유효하던 링크는 모두 무효가 되고, 그때는 재발급하면 된다.
저장한 것이 해시뿐이라 잃는 정보가 없다.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "issued_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("password_reset_tokens")
