"""MI 분석 요청과 잡 목록 응답의 식별자 필드를 확인하는 테스트.

MI 잡은 작업 단위가 아니라 영상 단위라서 작업 식별자가 없다. 영상은 `video_id`로 싣는다.
`work_item_id`는 같은 값을 싣는 호환 필드로 남는다. 이름만 보고 작업 식별자로 읽지 않도록
스키마에 사용 중단으로 표시한다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import mark_probed, new_project, upload_video


@pytest_asyncio.fixture(loop_scope="session")
async def fake():
    """가짜 MI를 넣고 끝나면 싱글턴을 비운다."""
    c = FakeMaiuClient()
    maiu_client.set_client(c)
    yield c
    maiu_client._client_singleton = None


def _h(token: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def test_dispatch_and_job_list_name_the_video(client: AsyncClient, admin_token: str, fake):
    """요청 응답과 잡 목록이 영상을 `video_id`로 싣고 호환 필드에 같은 값을 싣는지 확인한다."""
    pid = await new_project(client, admin_token, "maiu-dto")
    vid = await upload_video(client, admin_token, pid)
    await mark_probed(vid)

    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=_h(admin_token),
                          json={"video_ids": [vid]})
    assert r.status_code == 200, r.text
    (created,) = r.json()["created"]
    assert created["video_id"] == vid
    assert created["work_item_id"] == vid

    async with SessionLocal() as s:
        job = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == UUID(vid)))
    # 잡 식별자는 내부 정수 id를 하위 비트에 담는다. 같은 잡은 늘 같은 값이다.
    assert UUID(created["job_id"]).int & (2**62 - 1) == job.id

    r = await client.get(f"/projects/{pid}/maiu/jobs", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    row = next(j for j in r.json() if j["video_id"] == vid)
    assert row["job_id"] == created["job_id"] and row["work_item_id"] == vid


def test_schema_marks_work_item_id_deprecated():
    """OpenAPI 스키마가 `work_item_id`를 사용 중단으로 표시하고 `video_id`를 필수로 두는지 확인한다."""
    from reborn_contracts import MaiuJobDTO

    schema = MaiuJobDTO.model_json_schema()
    assert schema["properties"]["work_item_id"].get("deprecated") is True
    assert "video_id" in schema["required"]
