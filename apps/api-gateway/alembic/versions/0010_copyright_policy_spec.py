"""copyright_policy 값을 네 가지로 정리하고 권리 정보 컬럼을 더한다.

Revision ID: 0010
Revises: 0009

- 값을 `pre_cleared`·`require_review`·`mixed`·`external_pending` 네 가지로 통일한다.
- 기존 값은 아래처럼 옮긴다.
    `public` → `pre_cleared`: 공공 자료는 사전 정리와 뜻이 같다.
    `restricted` → `external_pending`: 제한 자료는 법무 검토가 필요하다.
    `pre_cleared` → `pre_cleared`: 그대로 둔다.
    `rights_review_required` → `require_review`: 이름만 바꾼다.
- 새 컬럼 `copyright_holder`·`clearance_document_url`을 더한다.
- 부분 인덱스 `idx_videos_rights_review`의 조건을 `require_review`로 바꾼다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 값에 의존하는 인덱스와 CHECK 제약을 잠시 푼다.
    op.drop_index("idx_videos_rights_review", table_name="videos")
    op.drop_constraint("ck_video_copyright_policy", "videos", type_="check")

    # 2. 기존 값을 새 값으로 옮긴다.
    op.execute("UPDATE videos SET copyright_policy = 'pre_cleared' "
               "WHERE copyright_policy = 'public'")
    op.execute("UPDATE videos SET copyright_policy = 'external_pending' "
               "WHERE copyright_policy = 'restricted'")
    op.execute("UPDATE videos SET copyright_policy = 'require_review' "
               "WHERE copyright_policy = 'rights_review_required'")

    # 3. 네 값으로 CHECK 제약을 다시 건다.
    op.create_check_constraint(
        "ck_video_copyright_policy",
        "videos",
        "copyright_policy IN ('pre_cleared','require_review','mixed','external_pending')",
    )

    # 4. 권리 정보 컬럼을 더한다.
    op.add_column("videos", sa.Column("copyright_holder", sa.Text))
    op.add_column("videos", sa.Column("clearance_document_url", sa.Text))

    # 5. `require_review`이면서 `pending`인 영상의 인덱스를 다시 만든다.
    op.create_index(
        "idx_videos_rights_review", "videos", ["project_id"],
        postgresql_where=sa.text(
            "copyright_policy = 'require_review' AND copyright_status = 'pending'"
        ),
    )


def downgrade() -> None:
    op.drop_index("idx_videos_rights_review", table_name="videos")
    op.drop_column("videos", "clearance_document_url")
    op.drop_column("videos", "copyright_holder")
    op.drop_constraint("ck_video_copyright_policy", "videos", type_="check")

    # 역방향 매핑은 정보를 잃는다. `mixed`·`external_pending`의 원래 값을 알 수 없다.
    op.execute("UPDATE videos SET copyright_policy = 'rights_review_required' "
               "WHERE copyright_policy = 'require_review'")
    op.execute("UPDATE videos SET copyright_policy = 'restricted' "
               "WHERE copyright_policy IN ('mixed','external_pending')")

    op.create_check_constraint(
        "ck_video_copyright_policy",
        "videos",
        "copyright_policy IN ('public','restricted','pre_cleared','rights_review_required')",
    )
    op.create_index(
        "idx_videos_rights_review", "videos", ["project_id"],
        postgresql_where=sa.text(
            "copyright_policy = 'rights_review_required' AND copyright_status = 'pending'"
        ),
    )
