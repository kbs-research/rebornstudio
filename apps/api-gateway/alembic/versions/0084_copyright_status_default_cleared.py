"""영상 저작권 상태의 컬럼 기본값을 등록 경로와 같은 `cleared`로 바꾼다.

바뀌는 것: `videos.copyright_status`의 server default를 `'pending'`에서 `'cleared'`로 바꾼다.
기존 행은 바꾸지 않는다. 기본값은 값을 넘기지 않은 INSERT에만 쓰인다.

등록은 정책과 관계없이 `cleared`다(분석 요청 버튼이 게이트다). 등록의 두 경로(업로드·버킷)는 이 값을 명시해서 넘기지만
이 컬럼의 기본값은 0003의 `pending`이라 새 영상의 초기 상태 값이 두 곳에서 서로 다르다.
값을 빠뜨린 새 경로·시드·원시 SQL은 `pending`을 조용히 받는다. `pending`은 제외했다가 되살린 영상만 가는
재판정 대기 상태라서 그 영상의 상태를 추적하기 어려워진다.
ORM 기본값은 `models.video.INITIAL_COPYRIGHT_STATUS`가 맞추고, 이 마이그레이션이 DB 쪽을 맞춘다.

기존 `pending` 행을 바꾸지 않는 이유: `pending`인 영상은 되살려서 재판정을 기다리는 영상이거나,
등록 때 `pending`을 명시하던 이전 시드다. 기본값만 보고는 둘을 가를 수 없다.
되살린 영상을 `cleared`로 바꾸면 사람의 판단을 건너뛰고 분석에 들어간다.

되돌리기: 기본값을 `'pending'`으로 되돌린다. ORM은 항상 값을 넘기므로 앱 동작은 그대로이고,
값을 넘기지 않는 원시 INSERT만 다시 `pending`을 받는다.
"""
from __future__ import annotations

from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("videos", "copyright_status", server_default="cleared")


def downgrade() -> None:
    op.alter_column("videos", "copyright_status", server_default="pending")
