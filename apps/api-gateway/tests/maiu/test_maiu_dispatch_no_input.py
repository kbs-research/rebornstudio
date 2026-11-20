"""경로가 없는 영상의 분석 요청 테스트. 요청하지 않고 읽을 수 있는 사유를 남긴다.

원본과 분석본 경로가 모두 없는 영상을 클라이언트에 넘기면 URI를 파싱하다 파이썬 예외가 난다.
그러면 그 예외 문장이 영상 행의 사유로 저장된다. 그래서 요청 전에 거른다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Video
from api_gateway.services.error_text import NO_INPUT_TEXT, analysis_error_text
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


async def test_dispatch_skips_video_without_input_uri(
    client: AsyncClient, admin_token: str, fresh_client
):
    """경로가 없는 영상은 잡을 만들지 않고 사유를 남긴다. 경로가 있는 영상은 그대로 요청한다."""
    pid = await new_project(client, admin_token, "no-input")
    hdr = {"Authorization": f"Bearer {admin_token}"}
    bare = await upload_video(client, admin_token, pid, title="bare", policy="pre_cleared")
    good = await upload_video(client, admin_token, pid, title="good", policy="pre_cleared")
    async with SessionLocal() as s:
        for vid in (bare, good):
            v = await s.get(Video, UUID(vid))
            v.probe_status = "passed"
            v.duration_seconds = 60.0
        v = await s.get(Video, UUID(bare))
        v.source_uri = None
        v.analysis_uri = None
        await s.commit()

    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=hdr, json={"limit": 10})
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        jobs = {str(v) for v in await s.scalars(
            select(AnalysisJob.video_id).where(AnalysisJob.project_id == UUID(pid)))}
        extra = (await s.get(Video, UUID(bare))).extra or {}
    assert bare not in jobs, "경로 없는 영상에 잡을 만들었다"
    assert good in jobs, "경로 없는 영상 하나가 나머지 요청을 막았다"
    assert extra.get("dispatch_error") == NO_INPUT_TEXT, extra


def test_legacy_none_uri_error_reads_as_missing_path() -> None:
    """이미 저장된 예외 원문(요청 실패 접두어와 None 예외)을 경로가 없다는 사유로 바꿔 표시한다.

    바꾼 사유를 다시 넣어도 같은 문구를 돌려준다.
    """
    said = analysis_error_text("MI 요청 실패 — 'NoneType' object has no attribute 'startswith'")
    assert said == NO_INPUT_TEXT, said
    assert analysis_error_text(said) == said
