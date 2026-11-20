"""문의(이의 제기) 테이블 `grievances`·`grievance_messages`를 지운다. RebornStudio는 문의 기능을 두지 않는다.

## 무엇이 바뀌나

RebornStudio에는 문의 제출·대화·처리 API와 화면, 문의 첨부가 없다. 두 테이블과 인덱스, CHECK 제약을 지운다.
대화(`grievance_messages`)가 문의를 참조하므로 대화부터 지운다.

## 이행

문의를 읽거나 쓰는 코드가 없는 버전이 배포된 뒤에 적용한다. 지운 문의와 대화는 돌아오지 않는다.
저장소의 `grievances/` 아래 첨부 파일은 지우지 않는다.

## 되돌리기

0094 시점의 빈 두 테이블을 컬럼·기본값·제약·인덱스까지 같게 다시 만든다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """대화 테이블과 문의 테이블을 차례로 지운다."""
    op.drop_table("grievance_messages")
    op.drop_table("grievances")


def downgrade() -> None:
    """0094 시점의 빈 `grievances`·`grievance_messages`를 다시 만든다."""
    op.create_table(
        "grievances",
        sa.Column("grievance_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        sa.Column("raised_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("work_item_id", UUID(as_uuid=True),
                  sa.ForeignKey("work_items.work_item_id", ondelete="SET NULL"), nullable=True),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("resolution", sa.Text, nullable=True),
        sa.Column("resolved_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attachments", JSONB, nullable=False, server_default="[]"),
        sa.CheckConstraint(
            "kind IN ('dispute','complaint','task_issue','guideline','technical','other')",
            name="ck_grievance_kind"),
        sa.CheckConstraint("status IN ('open','in_review','resolved','dismissed')",
                           name="ck_grievance_status"),
    )
    op.create_index("idx_grievance_project", "grievances", ["project_id", "status"])
    op.create_table(
        "grievance_messages",
        sa.Column("message_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("grievance_id", UUID(as_uuid=True),
                  sa.ForeignKey("grievances.grievance_id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("status_change", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("attachments", JSONB, nullable=False, server_default="[]"),
        sa.CheckConstraint(
            "status_change IS NULL OR status_change IN ('in_review','resolved','dismissed')",
            name="ck_grievance_message_status"),
    )
    op.create_index("ix_grievance_messages_grievance", "grievance_messages",
                    ["grievance_id", "created_at"])
