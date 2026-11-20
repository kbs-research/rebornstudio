"""체인을 잇는 빈 리비전. default 시드 프로젝트를 만들지 않는다.

Revision ID: 0038
Revises: 0037

설치마다 같은 이름의 프로젝트가 생기면 설치 기관과 무관한 값이 프로젝트 목록에 남는다. 그래서 만들지 않는다.
프로젝트는 관리자가 만들고, 정책 컬럼은 projects 테이블의 server_default가 채운다.
이전 버전의 이 리비전이 만든 시드 프로젝트는 0107이 지우거나 시드 값만 비운다.
"""
from collections.abc import Sequence

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """아무것도 하지 않는다."""


def downgrade() -> None:
    """아무것도 하지 않는다."""
