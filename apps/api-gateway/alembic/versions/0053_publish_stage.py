"""publish 단계: 재생본 생성과 최종화를 변환에서 분리한다.

컬럼을 따로 만드는 이유: publish도 워커로 보내는 잡이라 리스·펜싱·시도 상한이 필요하다. 이 셋은
`UPDATE … WHERE token = ?` 같은 원자적 조건이라 JSONB로는 만들 수 없다.
transcode와 같은 컬럼 구조를 쓰면 같은 안전장치를 그대로 쓴다.

expand만 한다. 기존 행은 'skipped'로 들어가고 바뀌는 동작이 없다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

_COLS = (
    ("publish_status", sa.String(20), "skipped"),
    ("publish_token", sa.Text(), None),
    ("publish_lease_until", sa.DateTime(timezone=True), None),
    ("publish_done_seconds", sa.Float(), None),
)


def upgrade() -> None:
    """publish 상태와 리스 컬럼을 더한다."""
    op.add_column("videos", sa.Column(
        "publish_status", sa.String(20), nullable=False, server_default="skipped"))
    op.add_column("videos", sa.Column("publish_token", sa.Text(), nullable=True))
    op.add_column("videos", sa.Column(
        "publish_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("videos", sa.Column("publish_done_seconds", sa.Float(), nullable=True))
    op.add_column("videos", sa.Column(
        "publish_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint(
        "ck_video_publish_status",
        "videos",
        "publish_status IN ('skipped','pending','queued','running','done','failed')",
    )
    # 단계 이력의 CHECK도 넓힌다. 넓히지 않으면 publish 런 행의 INSERT가 제약 위반으로 실패한다.
    # `deident`는 0049가 미리 넣어 두어 이미 있다.
    op.drop_constraint("ck_msr_stage", "media_stage_runs", type_="check")
    op.create_check_constraint(
        "ck_msr_stage", "media_stage_runs",
        "stage IN ('upload','probe','transcode','publish','register',"
        "'analyze','summarize','deident')",
    )

    # 큐 조회(pending 고르기)는 부분 인덱스를 쓴다. beat마다 전체 스캔이 도는 것을 막는다.
    op.create_index(
        "ix_videos_publish_pending", "videos", ["created_at"],
        postgresql_where=sa.text("publish_status = 'pending'"),
    )


def downgrade() -> None:
    """변경을 되돌린다. 데이터가 비어 있을 때만 안전하다(expand-contract)."""
    op.drop_index("ix_videos_publish_pending", table_name="videos")
    op.drop_constraint("ck_msr_stage", "media_stage_runs", type_="check")
    op.create_check_constraint(
        "ck_msr_stage", "media_stage_runs",
        "stage IN ('upload','probe','transcode','register','analyze','summarize','deident')",
    )
    op.drop_constraint("ck_video_publish_status", "videos", type_="check")
    for name in ("publish_attempts", "publish_done_seconds", "publish_lease_until",
                 "publish_token", "publish_status"):
        op.drop_column("videos", name)
