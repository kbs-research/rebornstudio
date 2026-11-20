"""삭제한 프로젝트를 id로 열 수 없는지 보는 테스트.

삭제는 soft delete라 행이 남는다. 목록에서만 빠지고 id로 계속 열리면 삭제가 접근을 막지 못한다.
최고관리자도, 남아 있던 멤버도 404 `project_not_found`를 받아야 한다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import User
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import add_member, new_project, upload_video


def _h(tok: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def test_deleted_project_is_404_for_everyone(client: AsyncClient, admin_token: str):
    """삭제한 프로젝트의 조회·수정·하위 목록이 최고관리자와 멤버 모두에게 404인지 확인한다."""
    pid = await new_project(client, admin_token, "del-guard")
    _, pa = await add_member(client, admin_token, pid, "project_admin")
    _, op = await add_member(client, admin_token, pid, "reviewer")
    _, lab = await add_member(client, admin_token, pid, "annotator")
    await upload_video(client, admin_token, pid)

    r = await client.delete(f"/projects/{pid}", headers=_h(admin_token))
    assert r.status_code == 204, r.text

    reads = [
        f"/projects/{pid}",
        f"/projects/{pid}/members",
        f"/projects/{pid}/videos",
        f"/projects/{pid}/stats",
        f"/projects/{pid}/pipeline/summary",
        f"/projects/{pid}/work/available-count",
    ]
    for tok in (admin_token, pa, op):
        for url in reads:
            got = await client.get(url, headers=_h(tok))
            assert got.status_code == 404, (url, got.status_code, got.text)
            assert got.json()["detail"] == "project_not_found", url
    got = await client.get(f"/projects/{pid}/work/available-count", headers=_h(lab))
    assert got.status_code == 404, got.text

    for tok in (admin_token, pa):
        r = await client.patch(f"/projects/{pid}", headers=_h(tok), json={"description": "x"})
        assert r.status_code == 404, r.text

    # 목록에서도 빠진다.
    listed = (await client.get("/projects", headers=_h(admin_token))).json()
    assert pid not in {p["project_id"] for p in listed}

    # 삭제한 프로젝트에는 초대할 수도 없다.
    outsider_uid, _ = await add_member(
        client, admin_token, await new_project(client, admin_token), "reviewer"
    )
    async with SessionLocal() as s:
        email = await s.scalar(select(User.email).where(User.user_id == UUID(outsider_uid)))
    r = await client.post(
        f"/projects/{pid}/members/invite", headers=_h(admin_token),
        json={"email": email, "role": "annotator"},
    )
    assert r.status_code == 404, r.text


async def test_deleted_project_update_leaves_no_audit_row(client: AsyncClient, admin_token: str):
    """삭제한 프로젝트를 고치려 해도 감사 로그에 `project.updated`가 남지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "del-audit")
    await client.delete(f"/projects/{pid}", headers=_h(admin_token))
    await client.patch(f"/projects/{pid}", headers=_h(admin_token), json={"description": "y"})
    from tests._helpers import events
    assert await events(event_type="project.updated", project_id=pid) == []


async def test_delete_audit_row_keeps_project_scope_and_name(client: AsyncClient, admin_token: str):
    """삭제 감사 행에 프로젝트 범위와 이름이 남는지 확인한다.

    지운 프로젝트는 목록에서 빠져 화면이 ID로 이름을 찾지 못한다.
    행에 이름이 없으면 무엇을 지웠는지 ID 앞자리로만 보이고, 범위가 비면 프로젝트로 걸러 볼 때 빠진다.
    """
    pid = await new_project(client, admin_token, "del-audit")
    name = (await client.get(f"/projects/{pid}", headers=_h(admin_token))).json()["name"]
    assert (await client.delete(f"/projects/{pid}", headers=_h(admin_token))).status_code == 204
    from tests._helpers import events
    rows = await events(event_type="project.deleted", target_id=pid)
    assert len(rows) == 1, rows
    assert rows[0]["project_id"] == pid
    assert rows[0]["payload"]["name"] == name
