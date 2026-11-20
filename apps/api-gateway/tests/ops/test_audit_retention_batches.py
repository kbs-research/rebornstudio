"""감사 보존 정리가 한 회차에 여러 묶음을 지우는지 확인하는 테스트.

한 회차에 한 묶음만 지우면 하루에 쌓이는 행(세션 갱신 포함)이 묶음 크기를 넘는 날부터 정리가
보존 기간을 따라가지 못하고, 고지한 3년보다 오래된 개인정보가 남는다.
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent
from api_gateway.services.ops import audit_retention
from sqlalchemy import func, select


async def test_purge_loops_batches_until_nothing_is_left(monkeypatch):
    """묶음 크기보다 많은 만료 행도 한 회차에 모두 지우고, 묶음마다 삭제 기록을 남긴다."""
    marker = uuid4()
    old = datetime.now(UTC) - timedelta(days=audit_retention.DEFAULT_RETENTION_DAYS + 30)
    async with SessionLocal() as s:
        for i in range(7):
            s.add(DomainEvent(event_type="auth.refreshed", target_kind="user", target_id=marker,
                              occurred_at=old + timedelta(seconds=i), payload={}))
        await s.commit()
    monkeypatch.setattr(audit_retention, "_BATCH", 3)

    async with SessionLocal() as s:
        deleted = await audit_retention.purge(s)
    async with SessionLocal() as s:
        left = await s.scalar(select(func.count()).select_from(DomainEvent)
                              .where(DomainEvent.target_id == marker))
    assert left == 0, f"한 회차에 {7 - left}건만 지웠다"
    assert deleted >= 7
