"""default 프로젝트의 정책 5컬럼을 명시 UPDATE로 채운다.

Revision ID: 0013
Revises: 0012

이전 버전의 0001이 만든 default 프로젝트는 INSERT가 정책 5컬럼을 넣지 않아 server_default에 기댄다.
그 프로젝트에 권장값을 채운다. `COALESCE`로 비어 있는 컬럼만 채우므로 이미 값이 있으면 덮어쓰지 않는다.
지금의 0001은 프로젝트를 만들지 않으므로 새 설치에서는 바꿀 행이 없다.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 기본 조직 default 프로젝트의 정책 5컬럼을 명시한다. server_default와 같은 값이라 멱등이다.
    op.execute("""
        UPDATE projects
        SET default_assignment_ttl_hours      = COALESCE(default_assignment_ttl_hours, 4),
            default_max_concurrent_assignments = COALESCE(default_max_concurrent_assignments, 5),
            conversations_enabled              = COALESCE(conversations_enabled, false),
            caption_min_tokens                 = COALESCE(caption_min_tokens, 50),
            reject_to_quarantine_threshold     = COALESCE(reject_to_quarantine_threshold, 3)
        WHERE slug = 'default'
          AND org_id = (SELECT org_id FROM organizations WHERE slug = 'default')
    """)


def downgrade() -> None:
    # 되돌릴 것이 없다. 값이 server_default와 같다.
    pass
