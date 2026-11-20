"""비밀번호 재설정 토큰의 발급자(`issued_by`)를 비울 수 있게 한다.

최고관리자가 비밀번호를 잃으면 화면에서 링크를 발급할 사람이 없다. 그래서 서버 명령
(`api_gateway.cli.recover_admin`)이 링크를 발급하는데, 그 발급에는 발급한 계정이 없다.
발급자가 빈 행은 서버 명령으로 발급한 토큰이다. 기존 행은 모두 발급자가 있으므로 이행할 것이 없다.

## 되돌리기

서버 명령으로 발급한 행(발급자가 빈 행)을 지운 뒤 NOT NULL을 되돌린다. 지운 링크는 쓸 수 없게 된다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """`password_reset_tokens.issued_by`의 NOT NULL을 푼다."""
    op.alter_column("password_reset_tokens", "issued_by",
                    existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    """발급자가 빈 행을 지우고 NOT NULL을 되돌린다."""
    op.execute("DELETE FROM password_reset_tokens WHERE issued_by IS NULL")
    op.alter_column("password_reset_tokens", "issued_by",
                    existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=False)
