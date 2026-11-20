"""문의 유형을 넓히고 이미지 첨부를 더한다.

- ck_grievance_kind에 작업자가 고르기 쉬운 유형 guideline·technical·other를 더한다.
- grievances.attachments JSONB(기본 [])에 첨부 이미지 목록 [{key,name}]을 담는다.

Revision ID: 0033
Revises: 0032
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

_OLD = "kind IN ('dispute','complaint','task_issue')"
_NEW = "kind IN ('dispute','complaint','task_issue','guideline','technical','other')"


def upgrade() -> None:
    op.drop_constraint("ck_grievance_kind", "grievances", type_="check")
    op.create_check_constraint("ck_grievance_kind", "grievances", _NEW)
    op.add_column(
        "grievances",
        sa.Column("attachments", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("grievances", "attachments")
    op.drop_constraint("ck_grievance_kind", "grievances", type_="check")
    op.create_check_constraint("ck_grievance_kind", "grievances", _OLD)
