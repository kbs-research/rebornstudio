"""운영 로그(`app_logs`)와 시스템 설정(`system_settings`) 테이블을 지운다.

## 무엇이 바뀌나

RebornStudio는 운영 로그를 DB에 쌓아 화면에서 읽는 기능과 시스템 설정 화면을 두지 않는다.
운영 로그는 컨테이너 로그로만 남긴다. 시스템 설정에 있던 보존 기간 두 값 중 감사 로그 보존 기간은
고지한 3년으로 고정하고(`services/ops/audit_retention.py`), 운영 로그 보존 기간은 테이블과 함께 없앤다.

## 이행

두 테이블을 읽거나 쓰는 코드가 없는 버전이 배포된 뒤에 적용한다. 지운 로그와 설정값은 돌아오지 않는다.

## 되돌리기

0095 시점의 빈 두 테이블을 컬럼·기본값·인덱스까지 같게 다시 만든다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """두 테이블을 지운다. 인덱스는 테이블과 함께 지워진다."""
    op.drop_table("app_logs")
    op.drop_table("system_settings")


def downgrade() -> None:
    """0095 시점의 빈 `system_settings`·`app_logs`를 다시 만든다."""
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", JSONB, nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=True),
    )
    op.create_table(
        "app_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("logger", sa.String(120), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("request_id", sa.String(32)),
        sa.Column("actor_user_id", sa.String(36)),
        sa.Column("path", sa.String(300)),
        sa.Column("method", sa.String(10)),
        sa.Column("status_code", sa.Integer),
        sa.Column("elapsed_ms", sa.Float),
        sa.Column("detail", JSONB),
    )
    op.create_index("idx_app_logs_at", "app_logs", [sa.text("at DESC")])
    op.create_index("idx_app_logs_level_at", "app_logs", ["level", sa.text("at DESC")])
    op.create_index("idx_app_logs_rid", "app_logs", ["request_id"])
