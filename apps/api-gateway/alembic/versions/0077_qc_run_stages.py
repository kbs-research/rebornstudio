"""검사 실행이 실제로 돈 단계를 기록한다.

필요한 이유: `findings`는 걸린 항목만 담는다. 통과한 검사와 하지 않은 검사는 둘 다 지적이 0건이라
화면이 구분할 수 없는데, 두 경우의 뜻은 다르다.

- 통과: 비속어 사전으로 검사했고 걸린 항목이 없다.
- 하지 않음: 사전이 비어 있어 검사하지 못했다.

`clip_length`도 기준값이 없으면 검사하지 않고, `deident`는 비식별화가 돌지 않았으면 결과를 남기지 않으며,
규칙에 걸리면 모델은 호출하지 않는다. 이 사실이 어디에도 남지 않으면 운영자는 체크리스트에서 모두 통과했다고 읽는다
(값이 없는 것과 빈 것은 다르다).

읽을 때 계산하지 않는 이유: 현재 설정으로 다시 계산하면 이전 실행의 기록이 틀린다.
어제는 사전이 비어 있었고 오늘 채웠다면 어제 실행도 비속어를 검사한 것으로 보인다. 실행 시점의 사실은 그때 적어야 한다.

`nullable`인 이유: 이 마이그레이션 이전 실행에는 기록이 없다. 빈 목록으로 채우면 단계를 하나도 돌지 않은 것으로
읽혀 기록이 틀린다. 화면은 `null`이면 "이 실행은 단계 기록이 없습니다"를 표시한다.

Revision ID: 0077
Revises: 0076
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("caption_qc_runs", sa.Column("stages", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("caption_qc_runs", "stages")
