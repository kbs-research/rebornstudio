"""트랜스코딩 파이프라인의 저장 기반인 상태·프로파일·리스·펜싱 토큰을 더한다.

스케일 아웃을 전제로 한 실행 모델은 영상 한 건에 task 하나를 두고,
워커가 작업을 가져갈 때 리스를 잡으며, 최종 반영은 펜싱 토큰 조건의 UPDATE로만 한다. 이 컬럼들이 그 재료다.

- transcode_status: 기본값은 파이프라인 밖을 뜻하는 'skipped'다. probe를 통과하면 판정(needs_transcode)이
  'pending'으로 올린다. 그래서 이 마이그레이션은 동작을 바꾸지 않는다.
- analysis_uri: 분석 입력 사본의 URI. source_uri는 바꾸지 않는다. 원본 경로는 저작권 판정과 감사의 근거다.
  MAIU 등록은 `analysis_uri or source_uri`를 쓴다.
- transcode_token/lease_until/attempts: 리스와 펜싱. 중복 실행이 결과를 덮어쓰지 못하게 한다.

Revision ID: 0047
Revises: 0046
"""
import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "videos",
        sa.Column("transcode_status", sa.String(20), nullable=False, server_default="skipped"),
    )
    op.add_column(
        "videos",
        sa.Column("transcode_profile", sa.String(10), nullable=False, server_default="auto"),
    )
    op.add_column("videos", sa.Column("analysis_uri", sa.Text()))
    op.add_column("videos", sa.Column("transcode_token", sa.Text()))
    op.add_column("videos", sa.Column("transcode_lease_until", sa.DateTime(timezone=True)))
    op.add_column(
        "videos",
        sa.Column("transcode_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_video_transcode_status", "videos",
        "transcode_status IN ('skipped','pending','queued','running','done','failed')",
    )
    op.create_check_constraint(
        "ck_video_transcode_profile", "videos",
        "transcode_profile IN ('auto','always','never')",
    )
    # dispatcher(30초)와 reclaim(5분)은 활성 행만 훑는다. 대부분의 행이 skipped·done이라 부분 인덱스로 둔다.
    op.create_index(
        "ix_videos_transcode_active", "videos", ["transcode_status"],
        postgresql_where=sa.text("transcode_status IN ('pending','queued','running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_videos_transcode_active", table_name="videos")
    op.drop_constraint("ck_video_transcode_profile", "videos")
    op.drop_constraint("ck_video_transcode_status", "videos")
    for col in ("transcode_attempts", "transcode_lease_until", "transcode_token",
                "analysis_uri", "transcode_profile", "transcode_status"):
        op.drop_column("videos", col)
