"""품질검수 실행에 소요 시간을 남긴다.

의미 구간 분절·캡셔닝·비식별화는 `media_stage_runs`에 시작·종료 시각이 남아 평균 처리 시간을 낼 수 있다.
`caption_qc_runs`는 판정과 지적만 담아서 품질검수의 처리 시간을 이 컬럼에 따로 남긴다.

`NULL`을 허용한다. 이 컬럼 이전의 실행은 측정하지 않은 값이라 0과 구분한다. 0으로 채우면 즉시 끝난 것으로 읽혀
평균을 끌어내린다. 화면은 값이 있는 건만 평균에 넣고 그 분모를 밝힌다.

밀리초로 둔다. 규칙만 도는 실행은 1초 미만이라 초 단위로 재면 모두 0이 된다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("caption_qc_runs", sa.Column("elapsed_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("caption_qc_runs", "elapsed_ms")
