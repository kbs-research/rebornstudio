"""인앱 알림(`notifications`) 테이블을 지운다. RebornStudio는 알림 센터를 두지 않는다.

## 무엇이 바뀌나

RebornStudio에는 알림 목록·읽음 처리 API와 화면, 알림을 만드는 호출(연결·분석 대상·연동 점검 실패와 복구, 반려, 격리,
가입 재신청, 분석 요청 대기)이 없다. 점검 결과의 전이는 감사 기록으로 남는다.
반려는 라벨러의 내 작업 목록에 재작업 행으로 보인다.

## 이행

알림을 읽거나 쓰는 코드가 없는 버전이 배포된 뒤에 적용한다. 지운 알림은 돌아오지 않는다.

## 되돌리기

0096 시점의 빈 테이블을 컬럼·기본값·인덱스까지 같게 다시 만든다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """테이블을 지운다. 인덱스는 테이블과 함께 지워진다."""
    op.drop_table("notifications")


def downgrade() -> None:
    """0096 시점의 빈 `notifications`를 다시 만든다."""
    op.create_table(
        "notifications",
        sa.Column("notification_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("link", sa.String(300), nullable=True),
    )
    op.create_index("idx_notification_user", "notifications", ["user_id", "read_at"])
