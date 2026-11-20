"""첫 스키마. organizations, users, projects, project_members 테이블과 기본 조직 한 행.

Revision ID: 0001
Revises:
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB, UUID

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # organizations
    op.create_table(
        "organizations",
        sa.Column("org_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("settings", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # users
    op.create_table(
        "users",
        sa.Column("user_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", CITEXT(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("global_role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("oauth_provider", sa.String(20)),
        sa.Column("oauth_subject", sa.String(255)),
        sa.Column("password_hash", sa.Text),
        sa.Column("email_verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("approval_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("approved_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("avatar_url", sa.Text),
        sa.Column("onboarded_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "global_role IN ('super_admin','staff','user')", name="ck_user_global_role"
        ),
        sa.CheckConstraint(
            "approval_status IN ('pending','active','suspended','rejected')",
            name="ck_user_approval_status",
        ),
        sa.UniqueConstraint("oauth_provider", "oauth_subject", name="uq_user_oauth"),
    )
    op.create_index(
        "idx_users_pending", "users", ["approval_status"],
        postgresql_where=sa.text("approval_status = 'pending'"),
    )

    # projects
    op.create_table(
        "projects",
        sa.Column("project_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.org_id"), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("contract_deadline", sa.Date),
        sa.Column("target_hours", sa.Float),
        sa.Column("target_deident_pct", sa.Float, nullable=False, server_default="90.0"),
        sa.Column("default_assignment_ttl_hours", sa.Integer, nullable=False, server_default="4"),
        sa.Column("default_max_concurrent_assignments", sa.Integer, nullable=False,
                  server_default="5"),
        sa.Column("conversations_enabled", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("caption_min_tokens", sa.Integer, nullable=False, server_default="50"),
        sa.Column("reject_to_quarantine_threshold", sa.Integer, nullable=False, server_default="3"),
        sa.Column("maiu_workspace_name", sa.String(100)),
        sa.Column("maiu_project_id", sa.String(100)),
        sa.Column("settings", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "slug", name="uq_project_org_slug"),
        sa.CheckConstraint(
            "status IN ('active','paused','completed','archived')", name="ck_project_status"
        ),
    )
    op.create_index("idx_projects_org_status", "projects", ["org_id", "status"])

    # project_members
    op.create_table(
        "project_members",
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), primary_key=True),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("expertise_genres", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("expertise_topics", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("max_concurrent_assignments", sa.Integer),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("approved_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "role IN ('project_admin','reviewer','annotator','viewer',"
            "'rights_reviewer','legal_reviewer')",
            name="ck_member_role",
        ),
        sa.CheckConstraint(
            "status IN ('pending','active','suspended','left','rejected')",
            name="ck_member_status",
        ),
    )
    op.create_index(
        "idx_members_user_active", "project_members", ["user_id"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "idx_members_project_active", "project_members", ["project_id"],
        postgresql_where=sa.text("status = 'active'"),
    )

    # 기본 조직 한 행을 만든다. 서버는 조직 관리 화면 없이 이 행을 찾아 쓰고 0101이 한 행으로 고정한다.
    # 프로젝트와 관리자 계정은 만들지 않는다. 관리자는 셋업 화면이, 프로젝트는 관리자가 만든다.
    op.execute("INSERT INTO organizations (slug, name) VALUES ('default', '기본 조직')")


def downgrade() -> None:
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("users")
    op.drop_table("organizations")
