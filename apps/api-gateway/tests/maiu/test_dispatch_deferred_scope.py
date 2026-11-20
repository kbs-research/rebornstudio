"""분석 요청이 다른 프로젝트 영상의 보류 표시를 지우지 않는지 보는 테스트.

요청 본문의 `video_ids`는 요청자가 고른다. 이번에 가져오지 않은 영상의 보류 표시를 지울 때
프로젝트 조건이 없으면, 한 프로젝트의 요청 권한으로 다른 프로젝트 영상의 표시를 지울 수 있다.
표시가 사라진 영상은 보류 사이클의 재요청 대상에서 빠져 요청 대기로 멈춘다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.media.video import merge_extra
from httpx import AsyncClient
from tests._helpers import new_project, upload_video


@pytest_asyncio.fixture(loop_scope="session")
async def fake_mi():
    """기본 가짜 MI를 넣고 끝나면 싱글턴을 비운다."""
    maiu_client.set_client(FakeMaiuClient())
    yield
    maiu_client._client_singleton = None


async def test_dispatch_keeps_other_projects_deferred_mark(
    client: AsyncClient, admin_token: str, fake_mi,
):
    """B 프로젝트 요청에 A 프로젝트 영상 id를 넣어도 A 영상의 보류 표시가 남는지 확인한다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid_a = await new_project(client, admin_token, "defer-a")
    pid_b = await new_project(client, admin_token, "defer-b")
    vid_a = await upload_video(client, admin_token, pid_a, policy="pre_cleared", probed=True)
    mark = {"at": "2026-01-01T00:00:00+00:00", "reason": "429", "kind": "throttle"}
    async with SessionLocal() as s:
        await merge_extra(s, UUID(vid_a), {"dispatch_deferred": mark})
        await s.commit()

    try:
        r = await client.post(
            f"/projects/{pid_b}/maiu/dispatch", headers=h, json={"video_ids": [vid_a]},
        )
        assert r.status_code == 200, r.text
        async with SessionLocal() as s:
            extra = (await s.get(Video, UUID(vid_a))).extra or {}
        assert extra.get("dispatch_deferred") == mark, "다른 프로젝트 요청이 보류 표시를 지웠다"
    finally:
        # 남은 표시는 다른 테스트의 보류 사이클(`redispatch_deferred`)이 전역에서 집어 간다.
        async with SessionLocal() as s:
            await merge_extra(s, UUID(vid_a), {}, remove=("dispatch_deferred",))
            await s.commit()
