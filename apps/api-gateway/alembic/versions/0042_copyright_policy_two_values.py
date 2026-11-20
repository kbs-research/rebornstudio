"""저작권 정책을 4종에서 2종으로 줄인다(2단 스크리닝 모델).

    `pre_cleared`: 검수를 생략한다. 등록하면 바로 통과하고, 분석 뒤 클립이 auto_cleared가 되어 곧바로 라벨링으로 간다.
    `review_required`: 1단(영상 스크리닝)을 통과하면 분석하고, 2단(클립 예외)에서 판정한 뒤 허가한 클립만 라벨링한다.

클립별로 다른 판정은 정책이 아니라 판정 결과이므로 정책값에서 뺀다. 전체 허가와 일부 제외는 검수자가 워크벤치에서 정한다.
그래서 영상 단위 검수(`require_review`)와 클립 단위 검수(`mixed`)가 하나의 정책이 된다.

기존 값은 아래처럼 옮긴다.
    `pre_cleared`: 그대로 둔다.
    `require_review` → `review_required`
    `mixed` → `review_required`: copyright_status는 유지한다. 이미 통과한 영상은 통과 상태로 남는다.
    `external_pending` → `review_required`: pending을 유지해 1단 대기로 둔다.

Revision ID: 0042
Revises: 0041
"""
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels = None
depends_on = None

_NEW_CHECK = "copyright_policy IN ('pre_cleared','review_required')"
_OLD_CHECK = "copyright_policy IN ('pre_cleared','require_review','mixed','external_pending')"


def upgrade() -> None:
    """옛 정책값을 review_required로 합치고 CHECK 제약을 2종으로 좁힌다."""
    op.drop_constraint("ck_video_copyright_policy", "videos", type_="check")
    op.execute(
        """
        UPDATE videos SET copyright_policy = 'review_required'
         WHERE copyright_policy IN ('require_review', 'mixed', 'external_pending')
        """
    )
    op.create_check_constraint("ck_video_copyright_policy", "videos", _NEW_CHECK)


def downgrade() -> None:
    """CHECK만 옛 4종으로 되돌린다. 합쳐진 원래 값은 정보가 사라져 되살릴 수 없다."""
    op.drop_constraint("ck_video_copyright_policy", "videos", type_="check")
    op.create_check_constraint("ck_video_copyright_policy", "videos", _OLD_CHECK)
