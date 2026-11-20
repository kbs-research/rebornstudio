"""export 재시도 횟수를 더해 일시 오류를 영구 실패로 만들지 않는다.

`process_pending` beat는 `pending`만 고른다. 그래서 boto3 예외 하나로 `failed`가 확정되면 그 export는 다시 처리되지 않는다.
일시 오류면 `pending`으로 되돌리는데, 그러려면 시도 횟수를 세야 무한 반복을 막을 수 있다.

`transcode_attempts`·`deident_attempts`와 같은 방식이다. 일시 오류는 횟수를 되돌려 주고 영구 오류만 센다.
"""
from alembic import op
import sqlalchemy as sa

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "exports",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("exports", "attempts")
