"""체인을 잇는 빈 리비전. super_admin 시드 계정을 만들지 않는다.

Revision ID: 0002
Revises: 0001

저장소에 적힌 비밀번호로 만든 super_admin이 설치마다 생기면 인터넷에 열린 서버에 누구나 아는
비밀번호로 로그인할 수 있다. 그래서 계정을 만들지 않는다.
첫 관리자는 셋업 화면이 만들고, 개발과 테스트의 관리자는 `scripts/dev/seed_admin.py`가 만든다.
이전 버전의 이 리비전이 만든 시드 계정은 0057이 정지하고 0107이 지운다.
"""
from collections.abc import Sequence

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """아무것도 하지 않는다."""


def downgrade() -> None:
    """아무것도 하지 않는다."""
