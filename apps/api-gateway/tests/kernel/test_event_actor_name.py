"""이벤트의 행위자 이름을 서버가 붙이는지 보는 테스트.

이름은 이벤트에 딸린 사실이라서 보는 사람의 소속과 무관하게 정해진다.
수행자에는 멤버가 아닌 사용자도 있다.
`super_admin`은 멤버가 아닌 채로 운영 액션을 하고, 나간 멤버의 기록도 남는다.
화면이 프로젝트 멤버 목록으로 이름을 찾으면 이런 행이 "알 수 없음"이나 id 앞 8자로 보인다.
"""
import uuid as _u
from datetime import UTC, datetime

from httpx import AsyncClient
from tests._helpers import events, new_project, upload_video


async def test_super_admin_actor_has_a_name(client: AsyncClient, admin_token: str):
    """멤버가 아닌 super_admin이 남긴 이벤트에도 이름이 붙는지 확인한다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import DomainEvent
    from api_gateway.services import audit
    from sqlalchemy import select

    pid = await new_project(client, admin_token, "actor")
    vid = await upload_video(client, admin_token, pid)   # video.uploaded 이벤트를 남긴다

    async with SessionLocal() as s:
        rows = list(await s.scalars(
            select(DomainEvent).where(
                DomainEvent.target_id == _u.UUID(vid), DomainEvent.actor_user_id.is_not(None),
            )
        ))
        assert rows, "행위자 있는 이벤트가 없어 이 검사가 무의미하다"
        out = await audit.with_actor_names(s, rows)
    for e in out:
        assert e.actor_name or e.actor_email, f"행위자 이름이 비었다: {e.event_type}"


async def test_system_events_have_no_actor(client: AsyncClient, admin_token: str):
    """행위자가 없는 이벤트는 이름도 None으로 두는지 확인한다. 없는 값을 만들어 내지 않는다.

    화면은 행위자가 없으면 `시스템`, 이름이 없으면 `기록 없음`으로 표시한다.
    두 표시를 구분하려면 두 경우의 값이 달라야 한다.
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import DomainEvent
    from api_gateway.services import audit

    async with SessionLocal() as s:
        # DB 기본값(event_id·occurred_at)을 직접 채운다.
        # 저장하지 않은 객체라서 기본값이 자동으로 붙지 않는다.
        ev = DomainEvent(event_id=_u.uuid4(), occurred_at=datetime.now(UTC),
                         event_type="x.test", target_kind="video",
                         actor_user_id=None, payload={})
        out = await audit.with_actor_names(s, [ev])
    assert out[0].actor_user_id is None
    assert out[0].actor_name is None and out[0].actor_email is None


async def test_one_query_for_many_rows(client: AsyncClient, admin_token: str):
    """행 여러 개에 이름을 붙일 때 사용자 조회가 한 번인지 확인한다.

    행마다 조회하면(N+1) 감사 로그가 가장 먼저 느려진다. 실행한 SQL 문 수를 엔진에서 센다.
    """
    from api_gateway.database import SessionLocal, engine
    from api_gateway.models import DomainEvent
    from api_gateway.services import audit
    from sqlalchemy import event

    pid = await new_project(client, admin_token, "actor-n")
    await upload_video(client, admin_token, pid)
    rows = await events(project_id=pid)
    actor = next(e["actor_user_id"] for e in rows if e["actor_user_id"])

    many = [
        DomainEvent(event_id=_u.uuid4(), occurred_at=datetime.now(UTC),
                    event_type="x.test", target_kind="video",
                    actor_user_id=_u.UUID(actor), payload={})
        for _ in range(20)
    ]
    statements: list[str] = []

    def _count(conn, cursor, statement, *_args) -> None:
        """엔진이 실행한 SQL 문을 모은다."""
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        async with SessionLocal() as s:
            out = await audit.with_actor_names(s, many)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)
    # 스무 행이 모두 같은 사람이어도 이름이 다 붙는다. 한 번 조회한 결과를 나눠 쓴다.
    assert len(out) == 20 and all(o.actor_name or o.actor_email for o in out)
    assert len(statements) == 1, f"행위자 조회가 {len(statements)}번이다: {statements}"
