"""공지(`announcements`) 테이블을 지운다. RebornStudio는 공지 기능을 두지 않는다.

## 무엇이 바뀌나

RebornStudio에는 공지 작성·조회 API와 화면, 공지 알림이 없다. 테이블과 인덱스, CHECK 제약을 함께 지운다.

## 이행

공지를 읽거나 쓰는 코드가 없는 버전이 배포된 뒤에 적용한다. 지운 공지는 돌아오지 않는다.

## 되돌리기

0093 시점의 빈 테이블을 컬럼·기본값·제약·인덱스까지 같게 다시 만든다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """테이블을 지운다. 인덱스와 제약은 테이블과 함께 지워진다."""
    op.drop_table("announcements")


def downgrade() -> None:
    """0093 시점의 빈 `announcements`를 인덱스·제약과 함께 다시 만든다."""
    op.create_table(
        "announcements",
        sa.Column("announcement_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("severity", sa.String(10), nullable=False, server_default="info"),
        sa.Column("source", sa.String(10), nullable=False, server_default="manual"),
        sa.Column("target_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audience", sa.String(10), nullable=False, server_default="all"),
        sa.CheckConstraint("severity IN ('info','warn','critical')",
                           name="ck_announcement_severity"),
        sa.CheckConstraint("source IN ('manual','system')", name="ck_announcement_source"),
        sa.CheckConstraint("audience IN ('all','workers','managers')",
                           name="ck_announcement_audience"),
    )
    op.create_index("ix_announcements_project", "announcements", ["project_id", "created_at"])
