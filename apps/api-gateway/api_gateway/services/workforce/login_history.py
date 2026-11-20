"""본인 로그인 이력 조회. 로그인·로그아웃·로그인 실패·로그인 잠금 이벤트를 본인에게 보여 준다.

로그인 이벤트 기록은 인증 서비스가 남긴다(`auth.service`). 이 모듈은 기록을 읽기만 한다.
로그인 이력에는 IP와 브라우저가 들어 있어 다른 사람의 이력은 돌려주지 않는다.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import DomainEvent, User

#: 로그인 이력에 보여 줄 이벤트 종류의 허용 목록. 새 `auth.*` 이벤트는 여기에 추가해야 나온다.
#: 세션 갱신(`auth.refreshed`)은 넣지 않으며 테스트로 보장한다.
_AUTH_EVENT_TYPES = ("auth.login", "auth.logout", "auth.login_failed", "auth.login_locked")


async def my_auth_events(db: AsyncSession, user: User, *, limit: int = 50) -> list[DomainEvent]:
    """본인의 최근 로그인·로그인 실패·로그인 잠금·로그아웃 domain_events를 최신순으로 돌려준다.

    ORM 객체를 돌려준다. DTO 직렬화는 라우터가 한다.
    """
    return list((await db.scalars(
        select(DomainEvent)
        .where(
            DomainEvent.actor_user_id == user.user_id,
            DomainEvent.event_type.in_(_AUTH_EVENT_TYPES),
        )
        .order_by(DomainEvent.occurred_at.desc())
        .limit(limit)
    )).all())
