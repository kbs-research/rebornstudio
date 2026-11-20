"""upload_status CHECK에서 'transcode_failed'를 뺀다.

0050의 백필이 먼저 돌아야 한다. 이 마이그레이션은 데이터가 비어 있다는 전제로 돈다.
남은 행이 하나라도 있으면 CHECK 생성이 실패하고, 이 실패가 안전장치다.
조용히 통과한 뒤 나중에 조회가 깨지는 것보다 여기서 멈추는 편이 안전하다.

expand-contract의 contract 단계이고, expand(0018에서 값 추가)와 짝을 이룬다.
"""
from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels = None
depends_on = None

_OLD = "upload_status IN ('pending','uploaded','failed','transcode_failed')"
_NEW = "upload_status IN ('pending','uploaded','failed')"


def upgrade() -> None:
    """CHECK를 좁힌다. 남은 행이 있으면 여기서 멈춘다(의도한 안전장치)."""
    op.drop_constraint("ck_video_upload_status", "videos", type_="check")
    op.create_check_constraint("ck_video_upload_status", "videos", _NEW)


def downgrade() -> None:
    """CHECK만 되돌리고 값은 다시 쓰지 않는다. 0050의 downgrade가 아무것도 하지 않는 것과 같은 이유다."""
    op.drop_constraint("ck_video_upload_status", "videos", type_="check")
    op.create_check_constraint("ck_video_upload_status", "videos", _OLD)
