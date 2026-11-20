"""videos.transcode_done_seconds를 더한다. 변환 진행률이다.

expand만 한다. 워커가 ffmpeg `-progress`로 받는 값(인코딩한 미디어 시간)을 하트비트에 실어 이 컬럼에 쌓는다.
`duration_seconds`를 분모로 쓰면 진행률이 된다.

벽시계 시간을 쓰지 않는 이유: 경과 시간만으로는 남은 시간을 알 수 없다.
또 인코더가 CPU에서 GPU로 바뀌어도 같은 기준으로 비교할 수 있는 값이 필요하다. 미디어 시간이 그 기준이다.

NULL 허용: 아직 진행 값을 받지 못했거나 이 컬럼 이전의 행이다. 화면은 분모나 분자가 없으면 진행률을 만들지 않는다.
"""
from alembic import op
import sqlalchemy as sa

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("transcode_done_seconds", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "transcode_done_seconds")
