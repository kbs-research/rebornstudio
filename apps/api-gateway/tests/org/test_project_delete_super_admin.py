"""프로젝트 삭제를 최고관리자만 하는지 보는 테스트.

삭제는 그 프로젝트의 영상·작업·결과물을 모두 목록에서 빼는 비가역에 가까운 조작이다.
프로젝트 관리자가 삭제할 수 있으면 한 계정의 실수나 탈취가 프로젝트 전체로 번진다.
"""
from httpx import AsyncClient
from tests._helpers import add_member, new_project


async def test_project_admin_cannot_delete_project(client: AsyncClient, admin_token: str):
    """프로젝트 관리자의 삭제는 403이고 프로젝트가 남으며, 최고관리자는 지울 수 있는지 확인한다."""
    pid = await new_project(client, admin_token, "del-pa")
    _, manager = await add_member(client, admin_token, pid, "project_admin")

    r = await client.delete(f"/projects/{pid}", headers={"Authorization": f"Bearer {manager}"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "not_super_admin"
    got = await client.get(f"/projects/{pid}", headers={"Authorization": f"Bearer {admin_token}"})
    assert got.status_code == 200, "프로젝트 관리자 요청이 프로젝트를 지웠다"

    r = await client.delete(f"/projects/{pid}", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 204, r.text
