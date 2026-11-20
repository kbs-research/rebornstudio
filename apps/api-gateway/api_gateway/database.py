"""API 프로세스가 쓰는 SQLAlchemy async engine과 세션 팩토리."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api_gateway.config import settings

# 풀 크기는 env로 정한다. 레플리카 N개가 여는 커넥션은 최대 N × (pool_size + max_overflow)개다.
# 이 값이 DB 상한을 넘지 않게 잡는다.
# `pool_pre_ping`은 풀에서 꺼낸 커넥션을 쓰기 전에 확인한다. DB가 재시작하거나 유휴 커넥션이
# 끊기면 풀에 남은 커넥션을 받은 첫 요청이 500으로 실패한다.
engine = create_async_engine(
    settings.database_url, echo=False, future=True,
    pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
