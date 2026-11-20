"""라벨에 AI 초안과 수정량을 남긴다. 수정률과 3단 개정 체인의 근거다.

수정률은 AI 초안 대비 작업자가 수정한 문자 수의 비율이다. 라벨러가 받은 AI 초안이 서버에 없으면
이 비율도, 개정 체인의 첫 단계(AI 초안, 작업자 제출본, 검수 수정본 순서)도 만들 수 없다.

작업을 처음 제출할 때 서버가 클립에서 초안을 읽어 라벨 행에 함께 남긴다(`services/work/edit_rate.py`).

- `ai_draft`: 라벨러가 받은 AI 초안 원문(앞뒤 공백만 제거).
- `edit_distance`: 정규화한 초안과 제출 캡션의 글자 단위 편집 거리.
- `draft_chars`: 정규화한 초안의 글자 수. 수정률의 분모다.

셋 다 `NULL`을 허용하고 백필하지 않는다. 과거 제출이 받은 초안은 서버에 남아 있지 않다.
클립의 지금 초안은 그때 초안과 다를 수 있어서 지금 초안으로 되살리면 지어낸 수가 된다.
과거 제출은 측정 전 상태로 두고 0과 구분한다(집계는 값이 있는 행만 더한다).

셋은 함께 있거나 함께 없다(CHECK). 초안 없이 거리만 있으면 무엇과 비교한 거리인지 알 수 없다.

확장(expand)만 한다. 이전 코드는 새 컬럼을 읽지 않으므로 코드 배포 전에 적용해도 안전하다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None

_CHECK = (
    "(ai_draft IS NULL AND edit_distance IS NULL AND draft_chars IS NULL)"
    " OR (ai_draft IS NOT NULL AND edit_distance IS NOT NULL"
    " AND draft_chars IS NOT NULL AND edit_distance >= 0 AND draft_chars > 0)"
)


def upgrade() -> None:
    op.add_column("labels", sa.Column("ai_draft", sa.Text(), nullable=True))
    op.add_column("labels", sa.Column("edit_distance", sa.Integer(), nullable=True))
    op.add_column("labels", sa.Column("draft_chars", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_label_edit_measure", "labels", _CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_label_edit_measure", "labels", type_="check")
    op.drop_column("labels", "draft_chars")
    op.drop_column("labels", "edit_distance")
    op.drop_column("labels", "ai_draft")
