"""MI 분석 요청이 규격 검증을 통과하고 길이를 아는 영상만 보내는지, 막은 사유를 돌려주는지 확인한다.

검증 전에는 변환·비식별화·재생본의 열 기본값이 모두 `skipped`라서 준비가 끝난 것처럼 보인다.
필요한 단계는 검증이 통과한 뒤에야 열린다. 그래서 검증 전에 요청하면 변환 전 원본이 MI로 나간다.
고른 영상이 게이트에 걸리면 사유 코드와 수를 돌려준다. 사유가 없으면 응답이 요청·실패·건너뜀이
모두 0인 200이 되고, 화면은 아무 일도 없었던 이유를 표시할 수 없다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Video
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


async def _set(video_id: str, **values) -> None:
    """영상 열을 바로 바꾼다."""
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(video_id))
        for k, val in values.items():
            setattr(v, k, val)
        await s.commit()


async def _jobs(pid: str) -> set[str]:
    """프로젝트에서 MI 잡이 만들어진 영상 ID 집합을 돌려준다."""
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(AnalysisJob.video_id).where(AnalysisJob.project_id == UUID(pid)))
        return {str(v) for v in rows}


async def _dispatch(client: AsyncClient, token: str, pid: str, vids: list[str]) -> dict:
    """고른 영상을 분석 요청하고 응답 본문을 돌려준다."""
    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=_h(token),
                          json={"video_ids": vids})
    assert r.status_code == 200, r.text
    return r.json()


async def test_unprobed_video_is_held_until_probe_passes(
    client: AsyncClient, admin_token: str, fake
):
    """규격 검증 전 영상은 요청하지 않고 사유 `not_probed`로 센다. 통과한 뒤에는 요청한다."""
    pid = await new_project(client, admin_token, "ready-gate")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, duration_seconds=75.0)   # 브라우저 업로드는 등록 때 길이를 보낸다

    body = await _dispatch(client, admin_token, pid, [vid])
    assert vid not in await _jobs(pid), "규격 검증 전 원본이 MI로 나갔다"
    assert body["requested"] == 0
    assert (body["held"], body["held_reasons"]) == (1, {"not_probed": 1})

    await mark_probed(vid)
    body = await _dispatch(client, admin_token, pid, [vid])
    assert vid in await _jobs(pid)
    assert body["held"] == 0 and len(body["created"]) == 1


async def test_probed_video_without_duration_is_held(
    client: AsyncClient, admin_token: str, fake
):
    """검증은 통과했지만 길이가 없는 영상을 막고 사유 `no_duration`을 돌려준다."""
    pid = await new_project(client, admin_token, "ready-gate-dur")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, probe_status="passed", duration_seconds=None)

    body = await _dispatch(client, admin_token, pid, [vid])
    assert vid not in await _jobs(pid)
    assert body["held_reasons"] == {"no_duration": 1}


async def test_preparing_video_reports_reason(client: AsyncClient, admin_token: str, fake):
    """재생본을 만드는 중인 영상을 고르면 사유 `preparing`과 수를 돌려준다."""
    pid = await new_project(client, admin_token, "ready-gate-prep")
    vid = await upload_video(client, admin_token, pid, probed=True)
    await _set(vid, publish_status="pending")

    body = await _dispatch(client, admin_token, pid, [vid])
    assert (body["requested"], body["skipped"], body["failed"]) == (0, 0, 0)
    assert (body["held"], body["held_reasons"]) == (1, {"preparing": 1})


async def test_mixed_selection_counts_every_video(client: AsyncClient, admin_token: str, fake):
    """고른 영상을 요청 수나 사유별 수 중 한 곳에 센다. 이미 요청한 영상의 사유는 `not_waiting`이다."""
    pid = await new_project(client, admin_token, "ready-gate-mix")
    ready = await upload_video(client, admin_token, pid, title="ready", probed=True)
    raw = await upload_video(client, admin_token, pid, title="raw")
    busy = await upload_video(client, admin_token, pid, title="busy", probed=True)
    await _set(busy, publish_status="pending")

    body = await _dispatch(client, admin_token, pid, [ready, raw, busy])
    assert body["requested"] == 1 and ready in await _jobs(pid)
    assert body["held_reasons"] == {"not_probed": 1, "preparing": 1}

    body = await _dispatch(client, admin_token, pid, [ready])
    assert body["held_reasons"] == {"not_waiting": 1}


async def test_unprobed_video_is_not_counted_as_ready(client: AsyncClient, admin_token: str):
    """반입 상태 집계가 검증 전 영상을 준비 중으로, 길이가 없는 검증 통과 영상을 막힘으로 센다.

    게이트와 집계가 다르면 "요청 대기 1"이 표시되는데 요청해도 아무것도 나가지 않는다.
    """
    pid = await new_project(client, admin_token, "ready-gate-bucket")
    raw = await upload_video(client, admin_token, pid, title="raw")
    await _set(raw, duration_seconds=75.0)
    nodur = await upload_video(client, admin_token, pid, title="nodur")
    await _set(nodur, probe_status="passed", duration_seconds=None)
    await upload_video(client, admin_token, pid, title="ok", probed=True)

    r = await client.get(f"/projects/{pid}/videos/buckets", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    counts = r.json()
    assert counts["ready"] == 1, counts
    assert counts["preparing"] == 1, counts
    assert counts["blocked"] == 1, counts
