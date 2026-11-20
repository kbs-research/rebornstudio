"""`connections`: 조직 단위 자격증명 레지스트리.

프리셋에 필요한 값(엔드포인트·버킷·자격증명)을 운영자가 채운다.
시크릿은 Fernet 암호문(`secret_ciphertext`)으로만 저장하고 평문은 DB에 두지 않는다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", PgUUID(as_uuid=True),
                  sa.ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("secret_ciphertext", sa.Text),
        sa.Column("secret_set_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", PgUUID(as_uuid=True), sa.ForeignKey("users.user_id")),
        sa.UniqueConstraint("org_id", "name", name="uq_connection_org_name"),
    )


def downgrade() -> None:
    op.drop_table("connections")
