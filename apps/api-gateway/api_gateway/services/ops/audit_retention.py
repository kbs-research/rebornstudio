"""감사 로그 보존 기간 적용. 보존 기간은 3년(1,095일)으로 고정한다.

3년은 개인정보 처리방침이 로그인 일시와 서비스 이용 기록에 고지한 보존 기간이다
(`features/auth/lib/privacy_terms.ts`의 `자동 수집` 행).
`auth.login` 행의 IP·User-Agent는 개인정보다.
고지를 바꾸면 동의 조건이 바뀌므로 실제 보존 기간을 고지에 맞춘다.
삭제 목적은 저장 공간 확보가 아니라 고지한 보존 기간 준수다.

행 수는 `영상당 14 + 작업당 2 + 활동 사용자의 근무 시간당 1(auth.refreshed)`로 어림한다.
행당 크기는 약 815 B다.
예를 들어 영상 7,500편·작업 15만 건, 100명이 하루 8시간씩 250일 일하면 3년에 약 100만 건, 815 MB다.

정리는 하루 한 번 돌며 묶음(`_BATCH`)을 시간 예산(`_BUDGET_S`) 안에서 되풀이한다.
한 묶음만 지우면 하루에 쌓이는 행이 묶음 크기를 넘는 날부터 보존 기간을 따라가지 못한다.

`domain_events`는 append-only이고 이 모듈이 유일한 삭제 경로다. 그래서 삭제 자체를
`system.audit_purged`로 남겨 정책에 따른 삭제와 그 밖의 삭제를 구분할 수 있게 한다.
이 기록도 보존 대상이지만 오래된 행부터 지우므로 삭제 기록은 끊기지 않는다.
"""
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import DomainEvent
from api_gateway.services import audit

#: 보존 일수. 개인정보 고지의 "수집일로부터 3년"과 같은 값이다.
#: 주의: 고지가 이 상수의 근거다. 한쪽을 바꾸면 `privacy_terms.ts`도 함께 바꾼다.
DEFAULT_RETENTION_DAYS = 1095

# 한 트랜잭션에서 지우는 최대 행 수. 상한이 없으면 첫 적용 때 수십만 행을 한 트랜잭션에서
# 지우는 동안 감사 테이블에 잠금이 걸린다.
_BATCH = 5_000
# 한 회차의 시간 예산(초). 넘으면 남은 행은 다음 회차가 지운다.
_BUDGET_S = 300


async def purge(db: AsyncSession) -> int:
    """보존 기간(3년)이 지난 감사 행을 지운다.

    오래된 행부터 `_BATCH`건씩 지우고 묶음마다 커밋한다. 지울 행이 없거나 시간 예산
    (`_BUDGET_S`)을 넘을 때까지 되풀이한다. 삭제 기록(`system.audit_purged`)은 묶음마다
    그 묶음과 같은 트랜잭션에 남긴다.

    Returns:
        지운 행 수. 지울 것이 없으면 0.
    """
    cutoff = datetime.now(UTC) - timedelta(days=DEFAULT_RETENTION_DAYS)
    started = time.monotonic()
    total = 0
    while time.monotonic() - started <= _BUDGET_S:
        n = await _purge_batch(db, cutoff)
        total += n
        if n < _BATCH:
            break
    return total


async def _purge_batch(db: AsyncSession, cutoff: datetime) -> int:
    """`cutoff`보다 오래된 감사 행을 한 묶음 지우고 삭제 기록과 함께 커밋한다.

    Returns:
        이 묶음에서 지운 행 수.
    """
    days = DEFAULT_RETENTION_DAYS
    doomed = (await db.scalars(
        select(DomainEvent.event_id)
        .where(DomainEvent.occurred_at < cutoff)
        .order_by(DomainEvent.occurred_at)
        .limit(_BATCH)
    )).all()
    if not doomed:
        return 0

    oldest = await db.scalar(
        select(func.min(DomainEvent.occurred_at))
        .where(DomainEvent.event_id.in_(doomed))
    )
    await db.execute(delete(DomainEvent).where(DomainEvent.event_id.in_(doomed)))
    # 유일한 삭제 경로라서 삭제 사실 자체를 남긴다. 이 행이 정책에 따른 삭제의 근거가 된다.
    audit.emit(
        db, event_type="system.audit_purged", target_kind="domain_event",
        payload={
            "deleted": len(doomed),
            "retention_days": days,
            "cutoff": cutoff.isoformat(),
            "oldest_deleted": oldest.isoformat() if oldest else None,
            "batch_limit": _BATCH,
            # 상한까지 지웠으면 남은 행이 더 있다고 표시해 잘린 사실을 드러낸다.
            "more_remaining": len(doomed) == _BATCH,
        },
    )
    await db.commit()
    return len(doomed)
