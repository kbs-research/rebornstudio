"""videos.probe_status를 더한다. MI 요청 전 규격 검사의 상태다.

Revision ID: 0039
Revises: 0038

Naver MI는 시간당 과금이라 깨진 파일·지원하지 않는 파일·규격을 넘는 파일을 보내면 비용만 든다.
그래서 MI 요청 전에 ffprobe로 규격(10GB 이하, 4시간 이하, 허용 코덱)을 확인한다.
상세 결과(추출 메타와 위반 사유)는 videos.metadata JSONB의 "probe" 키에 넣는다.
이 컬럼에는 워커 큐 조회용 상태만 둔다. JSONB 조회로 pending을 찾으면 인덱스를 쓰지 못한다.

기존 행은 모두 'pending'으로 시작한다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """probe_status 컬럼과 CHECK, pending 부분 인덱스를 더한다."""
    op.add_column(
        "videos",
        sa.Column(
            "probe_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.create_check_constraint(
        "ck_video_probe_status",
        "videos",
        "probe_status IN ('pending','passed','rejected','error')",
    )
    # 워커는 주기마다 probe 대기 영상만 고른다. 그래서 pending만 담는 부분 인덱스로 충분하고,
    # passed가 쌓여도 인덱스가 커지지 않는다.
    op.create_index(
        "ix_videos_probe_pending",
        "videos",
        ["project_id"],
        postgresql_where=sa.text("probe_status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_videos_probe_pending", table_name="videos")
    op.drop_constraint("ck_video_probe_status", "videos", type_="check")
    op.drop_column("videos", "probe_status")
