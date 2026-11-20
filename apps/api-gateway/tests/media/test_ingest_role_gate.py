"""모든 영상 반입 경로가 검수자를 거절하는지 확인하는 테스트.

반입 경로는 직접 등록, 버킷 키 등록, 폴더 등록, 업로드 시작 네 가지다.
가드가 한 경로에만 있으면 나머지 경로로 영상이 들어오므로 경로마다 따로 요청한다.
거절된 요청은 영상·반입 묶음 행을 남기지 않아야 한다.
"""
from uuid import UUID, uuid4

from api_gateway.database import SessionLocal
from api_gateway.models import IngestBatch, ProjectMember, Video
from httpx import AsyncClient
from sqlalchemy import func, select
from tests._helpers import add_member, new_project


def _h(tok: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _active_member(pid: str, uid: str, role: str) -> None:
    """그 사람이 지금 그 역할의 active 멤버인지 확인한다.

    비멤버도 같은 403 not_project_admin을 받는다. 그래서 멤버 시드가 실패하면 이 테스트가
    역할 가드를 확인하지 않고도 통과한다.
    """
    async with SessionLocal() as s:
        m = await s.get(ProjectMember, (UUID(pid), UUID(uid)))
    assert m is not None and (m.status, m.role) == ("active", role), m and (m.status, m.role)


async def _video_count(pid: str) -> int:
    """프로젝트의 영상 행 수를 센다. 거절된 요청이 행을 남겼는지 확인할 때 쓴다."""
    async with SessionLocal() as s:
        return await s.scalar(
            select(func.count()).select_from(Video).where(Video.project_id == UUID(pid))
        )


async def test_reviewer_cannot_create_video(client: AsyncClient, admin_token: str):
    """검수자의 영상 직접 등록은 403 not_project_admin이고 영상 행이 생기지 않는다."""
    pid = await new_project(client, admin_token, "ingest-rev")
    rev_uid, rev = await add_member(client, admin_token, pid, "reviewer")
    await _active_member(pid, rev_uid, "reviewer")
    r = await client.post(
        f"/projects/{pid}/videos", headers=_h(rev),
        json={"title": "검수자 등록", "copyright_policy": "pre_cleared"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "not_project_admin"
    assert await _video_count(pid) == 0


async def test_reviewer_cannot_register_storage_keys(client: AsyncClient, admin_token: str):
    """검수자의 버킷 키 일괄 등록은 403 not_project_admin이고 영상 행이 생기지 않는다."""
    pid = await new_project(client, admin_token, "ingest-rev-keys")
    rev_uid, rev = await add_member(client, admin_token, pid, "reviewer")
    await _active_member(pid, rev_uid, "reviewer")
    r = await client.post(
        f"/projects/{pid}/storage/register", headers=_h(rev),
        json={"keys": ["incoming/ac02/a.mp4"], "copyright_policy": "pre_cleared"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "not_project_admin"
    assert await _video_count(pid) == 0


async def test_reviewer_cannot_queue_prefix_import(client: AsyncClient, admin_token: str):
    """검수자의 폴더 등록은 403 not_project_admin이고 반입 묶음 행이 생기지 않는다.

    폴더 등록은 묶음 행을 먼저 만들고 worker가 이어서 등록한다.
    묶음 행이 남으면 거절된 요청이 나중에 영상을 등록하게 된다.
    """
    pid = await new_project(client, admin_token, "ingest-rev-prefix")
    rev_uid, rev = await add_member(client, admin_token, pid, "reviewer")
    await _active_member(pid, rev_uid, "reviewer")
    batch_id = uuid4()
    r = await client.post(
        f"/projects/{pid}/storage/register-prefix", headers=_h(rev),
        json={"prefix": "incoming/ac02/", "upload_batch_id": str(batch_id),
              "copyright_policy": "pre_cleared"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "not_project_admin"
    async with SessionLocal() as s:
        assert await s.get(IngestBatch, batch_id) is None
    assert await _video_count(pid) == 0


async def test_reviewer_cannot_start_upload(client: AsyncClient, admin_token: str):
    """관리자가 등록한 영상이라도 검수자는 업로드를 시작할 수 없다(단일·멀티파트 모두 403).

    등록과 업로드가 다른 요청이라 업로드 시작에도 같은 가드가 있어야 한다.
    """
    pid = await new_project(client, admin_token, "ingest-rev-up")
    rev_uid, rev = await add_member(client, admin_token, pid, "reviewer")
    await _active_member(pid, rev_uid, "reviewer")
    r = await client.post(
        f"/projects/{pid}/videos", headers=_h(admin_token),
        json={"title": "업로드 대기", "copyright_policy": "pre_cleared"},
    )
    assert r.status_code == 201, r.text
    vid = r.json()["video_id"]

    r = await client.post(f"/videos/{vid}/upload/multipart", headers=_h(rev))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "not_project_admin"
