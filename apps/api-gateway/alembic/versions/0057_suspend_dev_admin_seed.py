"""이전 버전의 0002가 만든 개발 편의용 super_admin 시드를 정지한다.

막는 실패: 이전 버전의 0002는 개발용 시드 비밀번호로 super_admin 계정을 만들었다. 그 비밀번호는 저장소 이력에 남아 있다.
운영에서 이 계정이 활성이면 인터넷에 열린 서버에 누구나 아는 비밀번호로 로그인할 수 있는 super_admin이 생긴다.
배포 때 사람이 비밀번호를 바꾸는 절차에 기대지 않고 마이그레이션이 계정을 정지한다.
지금의 0002는 계정을 만들지 않으므로 새 설치에서는 바꿀 행이 없다.

삭제하지 않고 정지하는 이유: `users`를 참조하는 외래 키가 여러 테이블(감사 이벤트·작업·클레임 등)에 있다.
이미 무언가를 만든 계정을 지우면 삭제가 실패하거나 이력이 끊긴다. `suspended`는 로그인을 막고
(`auth.service`가 `suspended`·`rejected`를 거부한다) 이력은 남긴다.

조건부로 정지하는 이유: 비밀번호를 바꾼 계정은 운영자가 쓰기로 한 실제 계정이므로 건드리지 않는다.
그래서 시드 해시 그대로일 때만 정지한다.
bcrypt 해시는 솔트가 달라 시드 해시와 같은 행은 시드 계정뿐이므로 이메일은 보지 않는다.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 이전 버전의 0002가 넣은 해시. 이 값과 같을 때만 개발 시드로 판단한다.
_SEED_HASH = "$2b$12$WGucEJr9tsp3esMvQ8z.HOOGoxnLz2lzjxNC4BjEgG3SFLx/ubgRm"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE users
           SET approval_status = 'suspended'
         WHERE password_hash = '{_SEED_HASH}'
           AND approval_status <> 'suspended'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE users
           SET approval_status = 'active'
         WHERE password_hash = '{_SEED_HASH}'
        """
    )
