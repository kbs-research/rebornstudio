"""저작권 검수 단계를 없앤다. 판정 이력 테이블과 저작권 점유·초안·한도 컬럼을 지운다.

## 무엇이 바뀌나

RebornStudio는 저작권 검수 단계가 없다. 클립은 `auto_cleared`로 생겨 분석 직후 라벨링 작업이 되고,
허가 상태에서 벗어나는 경로는 비식별화 미흡 신고(`legal_review`)와 그 신고의 제외 처리(`rejected`)뿐이다.
그래서 판정 이력(`rights_decisions`), 영상·클립의 저작권 점유와 초안, 프로젝트의 저작권 동시 한도를 지운다.
클립의 `rights_status`와 판정자·시각·사유 컬럼은 신고 처리가 쓰므로 남긴다.

## 이행

- 판정을 기다리던 클립(`pending`)은 `auto_cleared`로 바꾸고, 작업이 없으면 라벨링 작업을 만든다.
- 영상은 모두 `pre_cleared`·`cleared`로 바꾸고 정책 제약을 `pre_cleared` 하나로 줄인다.
- 클립 `rights_status`의 기본값을 `auto_cleared`로 바꾼다.

## 되돌리기

지운 테이블과 컬럼을 빈 값으로 다시 만들고 정책 제약과 기본값을 되돌린다. 이력과 점유는 돌아오지 않는다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """대기 클립을 허가하고 작업을 만든 뒤 저작권 검수 전용 테이블과 컬럼을 지운다."""
    op.execute("UPDATE clips SET rights_status = 'auto_cleared' WHERE rights_status = 'pending'")
    op.execute(
        "INSERT INTO work_items (video_id, project_id, clip_id, status) "
        "SELECT c.video_id, c.project_id, c.clip_id, 'available' FROM clips c "
        "WHERE c.rights_status IN ('auto_cleared','cleared','cleared_with_edit') "
        "AND NOT EXISTS (SELECT 1 FROM work_items w WHERE w.clip_id = c.clip_id)"
    )
    op.alter_column("clips", "rights_status", server_default="auto_cleared")
    op.execute("UPDATE videos SET copyright_policy = 'pre_cleared', copyright_status = 'cleared'")
    op.drop_constraint("ck_video_copyright_policy", "videos", type_="check")
    op.create_check_constraint(
        "ck_video_copyright_policy", "videos", "copyright_policy IN ('pre_cleared')")
    op.drop_table("rights_decisions")
    for col in ("rights_claimed_by", "rights_claimed_at", "rights_expires_at", "rights_draft"):
        op.drop_column("videos", col)
    for col in ("rights_claimed_by", "rights_claimed_at", "rights_expires_at", "rights_edit_notes"):
        op.drop_column("clips", col)
    op.drop_column("projects", "rights_max_concurrent")


def downgrade() -> None:
    """지운 테이블과 컬럼을 빈 값으로 다시 만들고 정책 제약과 기본값을 되돌린다."""
    op.add_column("projects", sa.Column(
        "rights_max_concurrent", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("clips", sa.Column("rights_edit_notes", sa.Text(), nullable=True))
    op.add_column("clips", sa.Column("rights_expires_at", sa.DateTime(timezone=True)))
    op.add_column("clips", sa.Column("rights_claimed_at", sa.DateTime(timezone=True)))
    op.add_column("clips", sa.Column(
        "rights_claimed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.user_id")))
    op.add_column("videos", sa.Column("rights_draft", postgresql.JSONB(), nullable=True))
    op.add_column("videos", sa.Column("rights_expires_at", sa.DateTime(timezone=True)))
    op.add_column("videos", sa.Column("rights_claimed_at", sa.DateTime(timezone=True)))
    op.add_column("videos", sa.Column(
        "rights_claimed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.user_id")))
    op.create_table(
        "rights_decisions",
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("videos.video_id", ondelete="CASCADE"), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True)),
        sa.Column("scope", sa.String(10), nullable=False, server_default="video"),
        sa.CheckConstraint(
            "decision IN ('cleared','rejected','cleared_with_edit','legal_review')",
            name="ck_rights_decision"),
    )
    op.create_index("idx_rights_decisions_video", "rights_decisions", ["video_id", "decided_at"])
    op.drop_constraint("ck_video_copyright_policy", "videos", type_="check")
    op.create_check_constraint(
        "ck_video_copyright_policy", "videos",
        "copyright_policy IN ('pre_cleared','review_required')")
    op.alter_column("clips", "rights_status", server_default="pending")
