"""pre_cleared 근거 컬럼을 더하고 기존 영상을 소급 백필한다.

pre_cleared(검수 생략)는 저작권 검수를 통째로 건너뛰므로 사전 허가의 근거를 남겨 감사할 수 있어야 한다.
새로 등록할 때는 근거를 선택 입력으로 받고, 근거 없이 이미 등록된 pre_cleared 영상은 소급 백필로 컬럼을 채운다.

Revision ID: 0043
Revises: 0042
"""
from alembic import op
import sqlalchemy as sa

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels = None
depends_on = None

_BACKFILL_REASON = "등록 시 일괄 사전허가 (소급)"


def upgrade() -> None:
    """precleared_reason 컬럼을 더하고 기존 pre_cleared 영상을 소급 백필한다."""
    op.add_column("videos", sa.Column("precleared_reason", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE videos SET precleared_reason = :r "
            "WHERE copyright_policy = 'pre_cleared' AND precleared_reason IS NULL"
        ).bindparams(r=_BACKFILL_REASON)
    )


def downgrade() -> None:
    """컬럼을 지운다. 백필한 근거도 함께 사라진다."""
    op.drop_column("videos", "precleared_reason")
