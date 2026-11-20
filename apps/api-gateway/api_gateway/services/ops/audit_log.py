"""한 사용자의 감사 기록(`domain_events`) 조회. 관리자 화면의 사용자 활동 이력이 부른다."""
from uuid import UUID

from reborn_contracts import DomainEventDTO
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import DomainEvent
from api_gateway.services import audit


async def user_activity(
    db: AsyncSession,
    user_id: UUID,
    *,
    limit: int,
    include_session_refresh: bool,
) -> list[DomainEventDTO]:
    """한 사용자가 행위자인 감사 로그를 최신순으로 읽고 행위자 이름을 붙인다."""
    stmt = select(DomainEvent).where(DomainEvent.actor_user_id == user_id)
    if not include_session_refresh:
        stmt = audit.exclude_session_refresh(stmt)
    return await audit.with_actor_names(db, list(await db.scalars(
        stmt.order_by(DomainEvent.occurred_at.desc()).limit(limit)
    )))
