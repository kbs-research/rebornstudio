"""클립이 이미 있는 영상을 MI 분석 요청에서 빼는지 확인한다.

영상 단위 티켓은 분석이 필요하다는 표시라서 클립이 생기면 쓰임이 끝난다.
클립을 만든 경로가 티켓을 지우지 못한 기존 데이터가 남아 있을 수 있다.
그 티켓으로 요청하면 이미 클립이 있는 영상이 MI에 다시 나가 과금되고 기존 클립과 겹치는 클립이 생긴다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, WorkItem
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, seed_clip_work, upload_video


@pytest_asyncio.fixture(loop_scope="session")
async def fake():
    """가짜 MI를 넣고 끝나면 싱글턴을 비운다."""
    c = FakeMaiuClient()
    maiu_client.set_client(c)
    yield c
    maiu_client._client_singleton = None


async def _jobs(pid: str) -> set[str]:
    """프로젝트에서 MI 잡이 만들어진 영상 ID 집합을 돌려준다."""
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(AnalysisJob.video_id).where(AnalysisJob.project_id == UUID(pid)))
        return {str(v) for v in rows}


async def test_dispatch_skips_a_video_that_already_has_clips(
    client: AsyncClient, admin_token: str, fake,
):
    """티켓이 남은 영상이라도 클립이 있으면 프로젝트 전체 요청과 선택 요청 모두 보내지 않는다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "dispatch-clipped")
    clipped = await upload_video(client, admin_token, pid, title="clipped", probed=True)
    fresh = await upload_video(client, admin_token, pid, title="fresh", probed=True)
    # 클립과 클립 작업만 넣고 영상 티켓은 남긴다. 티켓을 지우지 못한 기존 데이터의 모양이다.
    await seed_clip_work(pid, clipped, start=0.0, end=30.0, rights_status="auto_cleared")
    async with SessionLocal() as s:
        ticket = await s.scalar(select(WorkItem.work_item_id).where(
            WorkItem.video_id == UUID(clipped), WorkItem.clip_id.is_(None)))
    assert ticket is not None
    # 티켓이 남아 있어도 영상 목록의 MI 단계는 클립을 기준으로 분석 완료다.
    rows = (await client.get(f"/projects/{pid}/videos", headers=h)).json()
    stage = {r["video_id"]: r["pipeline_stage"] for r in rows}
    assert (stage[clipped], stage[fresh]) == ("analyzed", "awaiting_analysis")

    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=h, json={"limit": 10})
    assert r.status_code == 200, r.text
    assert await _jobs(pid) == {fresh}, "클립이 있는 영상이 MI로 나갔다"

    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=h,
                          json={"video_ids": [clipped]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requested"] == 0 and body["held_reasons"] == {"not_waiting": 1}
    assert clipped not in await _jobs(pid)
