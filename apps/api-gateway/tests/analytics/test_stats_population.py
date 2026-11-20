"""통계가 사람 작업만 세는지 보는 테스트. 영상 단위 디스패치 표식은 모집단에서 빠진다."""
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project


async def test_stats_excludes_dispatch_markers(client: AsyncClient, admin_token: str):
    """통계가 영상 단위 디스패치 표식을 세지 않는지 확인한다. 표식은 분석 대기 표시라서 사람 작업에서 빠진다.

    `work.service._human_workable`이 이 규칙을 정한다.
    영상 단위 work_item(clip_id IS NULL)은 이 영상에 MAIU 분석이 필요하다는 표식이다. 사람은 이 표식을 가져가지 않는다.
    표식은 승인될 수 없으므로 분모에 섞이면 완료율이 100%에 닿지 못한다.
    클립 4건 중 1건이 승인됐으면 완료율은 1/5(20%)가 아니라 1/4(25%)이다.
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem
    from tests._helpers import seed_clip_work, upload_video

    hdr = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "stats-marker")
    vid = await upload_video(client, admin_token, pid)   # 업로드가 영상 단위 표식을 만든다
    await seed_clip_work(pid, vid)                       # 클립 단위 1건

    async with SessionLocal() as s:                      # 전제 확인: 표식이 실제로 있다
        rows = (await s.execute(
            select(WorkItem).where(WorkItem.project_id == UUID(pid))
        )).scalars().all()
    assert len([w for w in rows if w.clip_id is None]) == 1, "표식이 없으면 이 테스트가 무의미하다"
    assert len([w for w in rows if w.clip_id is not None]) == 1

    body = (await client.get(f"/projects/{pid}/stats", headers=hdr)).json()
    assert body["total_work_items"] == 1, (
        f"영상 단위 표식이 섞였다 — {body['total_work_items']}건으로 셌다"
    )
    assert sum(body["work_by_status"].values()) == 1
    assert sum(body["work_hours_by_status"].values()) > 0, "시간 축이 클립 길이를 잃었다"
