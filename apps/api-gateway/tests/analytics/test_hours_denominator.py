"""진척의 시간 분모와 건수 분모가 같은 대상을 세는지 보는 테스트.

비식별화 신고로 제외된 클립의 작업은 닫혀 건수 분모에서 빠진다.
시간 분모에만 남으면 같은 진척 카드에서 두 분모가 서로 다른 대상을 센다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import WorkItem
from httpx import AsyncClient
from sqlalchemy import update
from tests._helpers import new_project, seed_clip_work, upload_video


async def test_rights_excluded_clip_leaves_both_denominators(client: AsyncClient, admin_token: str):
    """제외된 클립이 건수와 분량 어디에도 들지 않고 정상 클립 분량만 남는지 확인한다."""
    pid = await new_project(client, admin_token, "hours-den")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid, start=0.0, end=36.0)
    _c, excluded = await seed_clip_work(pid, vid, start=36.0, end=72.0, rights_status="rejected")
    async with SessionLocal() as s:
        await s.execute(update(WorkItem).where(WorkItem.work_item_id == UUID(excluded))
                        .values(status="cancelled"))
        await s.commit()

    kpi = (await client.get(
        f"/projects/{pid}/dashboard/business-kpi", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    assert kpi["total_count"] == 1
    assert kpi["total_hours"] == 0.01
