"""내보내기 처리가 한 번에 하나만 돌고 호출마다 한 건만 만드는지 보는 테스트.

패키지 조립은 승인분 전체를 읽고 zip을 쓰는 무거운 일이다. 여러 워커가 동시에 만들면 메모리와
대역폭을 나눠 쓰다 모두 느려지고, 한 호출이 여러 건을 이어 만들면 태스크 시간 제한에 걸려
만들던 건이 중단된다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Export
from api_gateway.services.exports import export_service
from httpx import AsyncClient
from tests._helpers import new_project


async def _make_export(client: AsyncClient, token: str, pid: str) -> str:
    """프로젝트에 내보내기를 하나 만들고 export_id를 돌려준다."""
    r = await client.post(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {token}"}, json={})
    assert r.status_code in (200, 201, 202), r.text
    return r.json()["export_id"]


async def test_overlapping_run_builds_nothing(client: AsyncClient, admin_token: str):
    """다른 실행이 처리하는 동안 겹친 실행은 아무 건도 잡지 않는다."""
    from api_gateway.services.joblock import single_runner

    pid = await new_project(client, admin_token, "exp-lock")
    eid = await _make_export(client, admin_token, pid)
    async with single_runner(export_service._LOCK_KEY) as got:
        assert got
        async with SessionLocal() as s:
            assert await export_service.process_pending(s) == 0
    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
        assert row.status == "pending" and (row.attempts or 0) == 0


async def test_default_call_builds_one_export(client: AsyncClient, admin_token: str):
    """beat가 기본값으로 부르면 대기 건이 여럿이어도 한 건만 만든다."""
    pid = await new_project(client, admin_token, "exp-one")
    await _make_export(client, admin_token, pid)
    await _make_export(client, admin_token, pid)
    async with SessionLocal() as s:
        assert await export_service.process_pending(s) == 1
