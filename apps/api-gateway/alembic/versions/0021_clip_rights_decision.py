"""클립 단위 저작권 결정 메타와 RightsDecision의 클립 범위를 더한다.

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """clips에 저작권 결정 메타 5컬럼을, rights_decisions에 clip_id·scope를 더하고 결정 값을 넓힌다."""
    # clips: 클립 단위 저작권 결정 메타
    op.add_column("clips", sa.Column("rights_decided_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("clips", sa.Column("rights_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("clips", sa.Column("rights_decision_reason", sa.Text(), nullable=True))
    op.add_column("clips", sa.Column("rights_decision_comment", sa.Text(), nullable=True))
    op.add_column("clips", sa.Column("rights_edit_notes", sa.Text(), nullable=True))

    # rights_decisions: 클립 범위의 결정을 담는다.
    op.add_column("rights_decisions", sa.Column("clip_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "rights_decisions",
        sa.Column("scope", sa.String(10), nullable=False, server_default="video"),
    )
    # decision 허용 값에 클립 판정 결과 상태를 더한다.
    op.drop_constraint("ck_rights_decision", "rights_decisions", type_="check")
    op.create_check_constraint(
        "ck_rights_decision", "rights_decisions",
        "decision IN ('cleared','rejected','quarantine','cleared_with_edit','legal_review')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_rights_decision", "rights_decisions", type_="check")
    op.create_check_constraint(
        "ck_rights_decision", "rights_decisions",
        "decision IN ('cleared','rejected','quarantine')",
    )
    op.drop_column("rights_decisions", "scope")
    op.drop_column("rights_decisions", "clip_id")
    for col in ("rights_edit_notes", "rights_decision_comment", "rights_decision_reason",
                "rights_decided_at", "rights_decided_by"):
        op.drop_column("clips", col)
