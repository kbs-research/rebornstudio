"""잡 러너의 단일 실행 락과 트랜잭션 범위의 임계 구역 락.

단일 실행 락은 전용 커넥션에서 잡고 푼다. 임계 구역 락은 트랜잭션이 끝날 때 함께 풀리므로
호출부의 세션으로 잡는다.

advisory lock은 커넥션(세션)에 묶인다. SQLAlchemy `AsyncSession`은 `commit()`마다 커넥션을
풀에 반환하고 다음 쿼리에서 새로 가져온다. 그래서 같은 `db` 세션으로 락을 잡으면 잡 처리 중의
`commit()`에서 락을 보유한 커넥션 A가 락을 가진 채 풀로 돌아간다.
해제는 다른 커넥션 B에서 실행되어 아무 락도 풀지 못하고 경고만 남긴다.

락을 보유한 커넥션이 풀에 남으면 이후 실행이 모두 skip된다. 예를 들어 pending 묶음이 있어도
`run_pending_imports`가 `batches: 0`을 돌려준다. 태스크마다 엔진을 dispose하는 워커에서도 같은
누출이 생기지만 커넥션이 닫히면서 증상이 드러나지 않는다.

advisory lock은 DB 단위가 아니라 Postgres 인스턴스(클러스터) 전역이다. 고정 상수 하나를 키로
쓰면 같은 인스턴스의 다른 DB끼리 서로를 막는다. 그러면 dev 워커가 락을 보유하는 동안 test DB의
러너가 실행되지 못한다. 그래서 키를 `(앱 키, 현재 DB 해시)` 두 정수로 준다.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from sqlalchemy import NullPool, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from api_gateway.config import settings

log = logging.getLogger(__name__)


async def lock_for_transaction(db: AsyncSession, name: str) -> None:
    """`name` 임계 구역을 `db`의 현재 트랜잭션이 끝날 때까지 잠근다.

    같은 이름을 잠그려는 다른 실행은 앞 트랜잭션의 커밋·롤백을 기다린다.

    전역 상한을 세고 그 수만큼 행을 고르는 디스패처가 세기 전에 부른다.
    `FOR UPDATE SKIP LOCKED`는 같은 행을 두 번 고르는 것만 막는다. 두 실행이 서로의 커밋 전에
    진행 중 건수를 세면 둘 다 상한만큼 골라 상한의 두 배가 발급된다.
    트랜잭션 범위 락이라 커밋·롤백에서 풀리고, 중간에 `commit()`하면 그 시점에 풀린다.
    `single_runner`와 달리 기다렸다가 진행하므로 뒤 실행은 앞 실행이 커밋한 상태를 보고 센다.
    """
    db_key = func.hashtext(func.current_database())
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(name), db_key)))


@asynccontextmanager
async def single_runner(key: int):
    """잡 러너가 하나만 실행되게 한다. 락을 얻었으면 True를 넘긴다.

    사용:
        async with single_runner(_KEY) as got:
            if not got:
                return {...}   # 다른 인스턴스가 실행 중이다
            ...

    블록이 끝날 때까지 커넥션을 유지하므로 중간에 `db.commit()`이 여러 번 일어나도 락이
    유지된다. 블록을 나갈 때 같은 커넥션에서 락을 해제한다.
    """
    # 호출마다 NullPool 전용 엔진을 만든다. 워커는 태스크마다 `asyncio.run`으로 새 이벤트 루프를
    # 만든다. asyncpg 커넥션은 자기를 만든 루프에 묶인다. 앞 태스크가 풀에 반납한 커넥션을
    # 다음 태스크의 루프가 꺼내 쓰면 InterfaceError("another operation is in progress")로 실패한다.
    # 모듈 전역 `database.engine`을 쓰면 주기 실행되는 reconciler가 매번 이 오류로 실패한다.
    # 락은 한 번에 하나만 잡으므로 풀이 필요 없다.
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            db_key = func.hashtext(func.current_database())
            got = bool(await conn.scalar(select(func.pg_try_advisory_lock(key, db_key))))
            try:
                yield got
            finally:
                if got:
                    released = await conn.scalar(select(func.pg_advisory_unlock(key, db_key)))
                    if not released:
                        # 해제 실패는 락을 잡은 커넥션이 바뀌었다는 뜻이다. 락이 누출됐을 수 있다.
                        log.error("advisory lock 해제 실패 key=%s — 락 누출 가능", key)
    finally:
        # 블록이 끝나면 엔진을 닫아 커넥션이 다음 이벤트 루프로 넘어가지 않게 한다.
        await eng.dispose()
