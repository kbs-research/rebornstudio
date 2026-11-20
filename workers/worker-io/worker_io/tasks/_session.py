"""태스크마다 새 engine과 세션을 열어 async 서비스 함수를 sync로 실행한다.

주의: asyncio.run은 호출마다 새 이벤트 루프를 만든다. FastAPI의 모듈 전역 `SessionLocal`은
engine 1개를 첫 루프에 묶으므로, 재사용하면 다음 호출에서 `attached to a different loop`
오류가 난다. 그래서 태스크마다 새 engine과 세션을 만들고 끝나면 dispose한다.
NullPool이라 커넥션도 매번 새로 만든다. 안전한 대신 느리지만 beat 주기(5s/5min)에서는
비용을 무시할 수 있다.

태스크를 시작할 때 등록한 저장소·분석 연결을 다시 읽는다(`platform_credentials.refresh_if_stale`).
워커 프로세스는 api의 갱신 루프를 돌지 않으므로, 여기서 읽지 않으면 기동할 때의 키를 계속 쓴다.
"""
import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from api_gateway.config import settings
from api_gateway.services import platform_credentials
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

T = TypeVar("T")


def _run_with_fresh_session(fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """태스크마다 새 engine으로 fn을 실행한다. asyncio.run의 루프 격리 문제를 피한다."""
    async def _go() -> T:
        """새 engine과 세션을 만들어 fn을 실행하고, 끝나면 engine을 dispose한다."""
        engine = create_async_engine(
            settings.database_url, echo=False, poolclass=NullPool,
        )
        session_local = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession,
        )
        try:
            # 갱신은 따로 연 세션에서 한다. 태스크 본문의 트랜잭션에 갱신 조회가 섞이지 않게 한다.
            async with session_local() as creds_session:
                await platform_credentials.refresh_if_stale(creds_session)
            async with session_local() as session:
                return await fn(session)
        finally:
            await engine.dispose()
    return asyncio.run(_go())
