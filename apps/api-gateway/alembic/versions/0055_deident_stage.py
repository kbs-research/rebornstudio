"""비식별화 단계의 컬럼을 만든다. 프로젝트 스위치와 영상별 비식별화 상태·리스다.

`deident_enabled`가 켜졌는데 비식별화를 하지 못하면 파이프라인이 멈춘다. 건너뛰면 원본 얼굴이
클라우드 분석기로 나가기 때문이다. 그래서 skipped(쓰지 않음)와 failed(쓰기로 했는데 실패함)를 다른 값으로 둔다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """프로젝트 스위치와 영상별 비식별화 상태·리스를 더한다."""
    op.add_column("projects", sa.Column(
        "deident_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("videos", sa.Column(
        "deident_status", sa.String(20), nullable=False, server_default="skipped"))
    op.add_column("videos", sa.Column("deident_token", sa.Text(), nullable=True))
    op.add_column("videos", sa.Column(
        "deident_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("videos", sa.Column("deident_done_seconds", sa.Float(), nullable=True))
    op.add_column("videos", sa.Column(
        "deident_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint(
        "ck_video_deident_status", "videos",
        "deident_status IN ('skipped','pending','queued','running','done','failed')",
    )
    op.create_index(
        "ix_videos_deident_pending", "videos", ["created_at"],
        postgresql_where=sa.text("deident_status = 'pending'"),
    )


def downgrade() -> None:
    """변경을 되돌린다. 데이터가 비어 있을 때만 안전하다(expand-contract)."""
    op.drop_index("ix_videos_deident_pending", table_name="videos")
    op.drop_constraint("ck_video_deident_status", "videos", type_="check")
    for name in ("deident_attempts", "deident_done_seconds", "deident_lease_until",
                 "deident_token", "deident_status"):
        op.drop_column("videos", name)
    op.drop_column("projects", "deident_enabled")
