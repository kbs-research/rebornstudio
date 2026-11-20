"""원격 비식별화 서버 연결과 프로젝트의 서버 선택을 지운다.

RebornStudio는 비식별화 모델(`essential-yunet`)을 비식별화 워커 안에서 돌리므로 원격 서버에 잡을 보내지 않는다.
서비스 `deid` 연결은 쓰는 곳이 없고 계약의 서비스 목록에서도 빠졌으므로 행을 지운다.
프로젝트 `settings`의 `deident` 키는 그 연결의 이름(`connection_name`) 하나만 담으므로 키째 지운다.
비식별화 켜기(`projects.deident_enabled`)와 연결의 점검 기록 컬럼(`last_capabilities`)은 그대로 둔다.

## 되돌리기

하지 않는다. 지운 연결과 서버 선택은 돌아오지 않는다.
"""
from alembic import op

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """서비스 `deid` 연결 행과 프로젝트 설정의 `deident` 키를 지운다."""
    op.execute("DELETE FROM connections WHERE provider = 'deid'")
    op.execute(
        "UPDATE projects SET settings = settings - 'deident' "
        "WHERE settings ? 'deident'"
    )


def downgrade() -> None:
    """되돌리지 않는다. 지운 행과 키는 돌아오지 않는다."""
