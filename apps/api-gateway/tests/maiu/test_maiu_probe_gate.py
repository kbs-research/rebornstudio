"""ffprobe 게이트 테스트. dispatch는 규격 위반(rejected) 영상을 MI에 보내지 않는다.

probe 배치가 rejected로 확정한 영상을 dispatch가 보내면 규격 검증 게이트가 적용되지 않는다.
MI는 시간당 과금이라 비용도 낭비된다.
rejected만 차단하고 passed는 정상으로 보내는지 확인한다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Video
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, upload_video


@pytest_asyncio.fixture(loop_scope="session")
async def fresh_client():
    """dispatch용 FakeMaiuClient를 주입하고 끝나면 싱글턴을 비운다."""
    maiu_client.set_client(FakeMaiuClient())
    yield
    maiu_client._client_singleton = None


async def _set_probe(video_id: str, status: str) -> None:
    """영상의 probe_status를 바로 바꾼다."""
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(video_id))
        v.probe_status = status
        if status == "passed":
            v.duration_seconds = v.duration_seconds or 60.0
        await s.commit()


async def _dispatched_video_ids(pid: str) -> set[str]:
    """프로젝트에서 AnalysisJob이 만들어진 영상 ID 집합을 돌려준다."""
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(AnalysisJob.video_id).where(AnalysisJob.project_id == UUID(pid))
        )
        return {str(v) for v in rows}


async def test_dispatch_excludes_probe_rejected_video(
    client: AsyncClient, admin_token: str, fresh_client
):
    """dispatch가 rejected 영상을 빼고 passed 영상은 정상으로 보낸다."""
    pid = await new_project(client, admin_token, "probe-gate")
    hdr = {"Authorization": f"Bearer {admin_token}"}
    bad = await upload_video(client, admin_token, pid, title="bad", policy="pre_cleared")
    good = await upload_video(client, admin_token, pid, title="good", policy="pre_cleared")
    await _set_probe(bad, "rejected")
    await _set_probe(good, "passed")

    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=hdr, json={"limit": 10})
    assert r.status_code == 200, r.text
    dispatched = await _dispatched_video_ids(pid)
    assert good in dispatched
    assert bad not in dispatched, "규격 위반(rejected) 영상이 MI에 태워졌다"


async def test_dispatch_by_ids_skips_rejected(
    client: AsyncClient, admin_token: str, fresh_client
):
    """rejected 영상을 ID로 지정해 요청해도 보내지 않는다. 게이트는 선택 요청도 막는다."""
    pid = await new_project(client, admin_token, "probe-gate-2")
    hdr = {"Authorization": f"Bearer {admin_token}"}
    bad = await upload_video(client, admin_token, pid, title="bad", policy="pre_cleared")
    await _set_probe(bad, "rejected")

    r = await client.post(
        f"/projects/{pid}/maiu/dispatch", headers=hdr, json={"video_ids": [bad]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == []
    assert await _dispatched_video_ids(pid) == set()
