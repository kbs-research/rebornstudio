"""라벨러에게 버킷 경로와 처리 이력을 내보내지 않는지 확인하는 테스트.

영상 상세 엔드포인트는 라벨러에게도 열어 둔다. 라벨링 워크벤치의 재생기가 이 응답의
`title` 하나를 읽으므로 403으로 바꾸면 작업 화면이 깨진다. 그래서 접근 대신 필드를 제한한다.
준비 타임라인은 403으로 막는다.
영상 상세는 필드를 가려서 스토리지 경로(`source_uri`)가 라벨러에게 나가지 않게 한다.
"""
import uuid

from api_gateway.database import SessionLocal
from api_gateway.models import Video
from httpx import AsyncClient
from tests._helpers import add_member, new_project, upload_video


async def _seed(vid: str) -> None:
    """영상에 스토리지 경로와 처리 메타를 넣는다."""
    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        v.source_uri = "s3://bucket/test data/보도/news-0001.mp4"
        v.extra = {"key": "intake/x/source", "probe": {"video_codec": "h264"},
                   "transcode": {"master_key": "derived/x/master.mp4"}}
        await s.commit()


async def test_labeler_gets_no_storage_path(client: AsyncClient, admin_token: str):
    """라벨러에게 주는 영상 상세에서 버킷 경로와 처리 이력을 비운다."""
    pid = await new_project(client, admin_token, "vfield")
    vid = await upload_video(client, admin_token, pid)
    await _seed(vid)
    _, lab = await add_member(client, admin_token, pid, "annotator")

    r = await client.get(f"/videos/{vid}", headers={"Authorization": f"Bearer {lab}"})
    # 요청은 200으로 받는다. 막으면 워크벤치가 깨진다. 가릴 대상은 응답에 실린 값이다.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_uri"] is None, "라벨러에게 버킷 경로가 나갔다"
    assert body["metadata"] == {}, "라벨러에게 처리 이력이 나갔다"
    # 재생기가 쓰는 값은 그대로 둔다. 이 값까지 가리면 작업을 할 수 없다.
    assert body["title"]


async def test_admin_still_sees_it(client: AsyncClient, admin_token: str):
    """관리자에게는 버킷 경로와 처리 이력을 그대로 보여 준다. 가림은 역할에 따라 적용한다."""
    pid = await new_project(client, admin_token, "vfield-admin")
    vid = await upload_video(client, admin_token, pid)
    await _seed(vid)
    body = (await client.get(f"/videos/{vid}",
                             headers={"Authorization": f"Bearer {admin_token}"})).json()
    assert body["source_uri"].startswith("s3://")
    assert body["metadata"]["transcode"]["master_key"]


async def test_the_list_is_covered_too(client: AsyncClient, admin_token: str):
    """목록에서도 같은 값을 가린다. 상세만 가리면 목록으로 같은 값이 나간다.

    같은 `_to_dto`를 상세와 목록 두 곳이 쓴다.
    """
    pid = await new_project(client, admin_token, "vfield-list")
    vid = await upload_video(client, admin_token, pid)
    await _seed(vid)
    _, lab = await add_member(client, admin_token, pid, "annotator")

    r = await client.get(f"/projects/{pid}/videos", headers={"Authorization": f"Bearer {lab}"})
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body["items"] if isinstance(body, dict) else body
    row = next(x for x in rows if x["video_id"] == vid)
    assert row["source_uri"] is None and row["metadata"] == {}


async def test_reviewer_still_sees_it(client: AsyncClient, admin_token: str):
    """검수자에게는 그대로 보여 준다. 검수자는 작업 결과를 보는 역할이라 가림 대상이 라벨러뿐이다."""
    pid = await new_project(client, admin_token, "vfield-rev")
    vid = await upload_video(client, admin_token, pid)
    await _seed(vid)
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    body = (await client.get(f"/videos/{vid}",
                             headers={"Authorization": f"Bearer {rev}"})).json()
    assert body["source_uri"].startswith("s3://"), "검수자에게까지 가렸다"


# ── 멤버 목록·집계도 같은 방식으로 막는다 ──────────────────────────────────

async def test_labeler_cannot_list_coworkers(client: AsyncClient, admin_token: str):
    """라벨러의 프로젝트 멤버 목록 조회를 403으로 거절한다. 화면만 막으면 동료 전원의 이메일이 응답으로 나간다."""
    pid = await new_project(client, admin_token, "vfield-members")
    _, lab = await add_member(client, admin_token, pid, "annotator")
    r = await client.get(f"/projects/{pid}/members", headers={"Authorization": f"Bearer {lab}"})
    assert r.status_code == 403 and r.json()["detail"] == "not_work_result_viewer"


async def test_labeler_cannot_read_project_stats(client: AsyncClient, admin_token: str):
    """라벨러의 프로젝트 전체 집계(영상·클립·작업·인원) 조회를 403으로 거절한다.

    라벨러의 큐와 상관없는 값이다.
    """
    pid = await new_project(client, admin_token, "vfield-stats")
    _, lab = await add_member(client, admin_token, pid, "annotator")
    r = await client.get(f"/projects/{pid}/stats", headers={"Authorization": f"Bearer {lab}"})
    assert r.status_code == 403 and r.json()["detail"] == "not_work_result_viewer"


async def test_labeler_cannot_read_business_kpi(client: AsyncClient, admin_token: str):
    """라벨러의 사업 KPI 조회도 403으로 거절한다.

    이 엔드포인트가 `stats`를 호출하므로 한쪽만 막으면 다른 쪽으로 같은 값이 나간다.
    """
    pid = await new_project(client, admin_token, "vfield-kpi")
    _, lab = await add_member(client, admin_token, pid, "annotator")
    r = await client.get(f"/projects/{pid}/dashboard/business-kpi",
                         headers={"Authorization": f"Bearer {lab}"})
    assert r.status_code == 403
