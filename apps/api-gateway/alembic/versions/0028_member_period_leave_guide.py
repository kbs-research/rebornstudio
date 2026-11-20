"""project.guide_md와 project_members의 참여 기간·이탈·가이드 확인 필드를 더한다.

Revision ID: 0028
Revises: 0027
"""
import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 프로젝트 작업 가이드(마크다운)
    op.add_column("projects", sa.Column("guide_md", sa.Text, nullable=True))
    # 멤버 참여 기간(초대 시작일과 종료일)
    op.add_column("project_members", sa.Column("start_date", sa.Date, nullable=True))
    op.add_column("project_members", sa.Column("end_date", sa.Date, nullable=True))
    # 조기 이탈. status='left'는 기존 제약이 이미 허용한다.
    op.add_column("project_members", sa.Column("leave_reason", sa.Text, nullable=True))
    op.add_column("project_members", sa.Column("left_at", sa.DateTime(timezone=True), nullable=True))
    # 가이드 확인 시각
    op.add_column("project_members", sa.Column("guide_ack_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("project_members", "guide_ack_at")
    op.drop_column("project_members", "left_at")
    op.drop_column("project_members", "leave_reason")
    op.drop_column("project_members", "end_date")
    op.drop_column("project_members", "start_date")
    op.drop_column("projects", "guide_md")
