"""프로젝트 일반 설정 변경을 관리자만 할 수 있는지 역할별로 보는 테스트.

설정은 거버넌스 영역이라 관리자가 맡는다(`require_project_admin`).
검수자와 라벨러는 설정을 바꿀 수 없다. 거절된 요청은 값도 감사 기록도 남기지 않아야 한다.
프로젝트 관리자는 super_admin이 아니라 멤버로 넣어 프로젝트 역할만으로 통과하는지 확인한다.
"""
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent, ProjectMember
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import add_member, new_project

#: 일반 설정 화면이 보내는 필드와 기본값이 아닌 새 값. 필드마다 따로 보낸다.
#: 한 요청에 몰아 보내면 한 필드가 가드를 통과해도 나머지 필드의 403에 가려진다.
_CHANGES = [
    {"name": "역할 가드 확인용 이름"},
    {"description": "역할 가드 확인용 설명"},
    {"guide_md": "# 역할 가드 확인용 가이드"},
    {"target_hours": 321},
]


def _h(tok: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _active_member(pid: str, uid: str, role: str) -> None:
    """그 사람이 지금 그 역할의 active 멤버인지 확인한다.

    비멤버도 같은 403 not_project_admin을 받으므로, 멤버 시드가 실패하면 이 테스트가 역할 가드를 확인하지 않고도 통과한다.
    """
    async with SessionLocal() as s:
        m = await s.get(ProjectMember, (UUID(pid), UUID(uid)))
    assert m is not None and (m.status, m.role) == ("active", role), m and (m.status, m.role)


async def _updated_events(pid: str) -> list[DomainEvent]:
    """프로젝트의 `project.updated` 감사 행을 시간순으로 돌려준다."""
    async with SessionLocal() as s:
        return list((await s.scalars(
            select(DomainEvent).where(
                DomainEvent.project_id == UUID(pid),
                DomainEvent.event_type == "project.updated",
            ).order_by(DomainEvent.occurred_at)
        )).all())


@pytest.mark.parametrize("role", ["reviewer", "annotator"])
async def test_non_admin_role_cannot_change_general_settings(
    client: AsyncClient, admin_token: str, role: str,
):
    """검수자·라벨러의 일반 설정 변경이 필드마다 403 not_project_admin인지 확인한다.

    값이 그대로이고 `project.updated` 감사 행도 생기지 않는다.
    """
    pid = await new_project(client, admin_token, f"gate-{role[:3]}")
    uid, tok = await add_member(client, admin_token, pid, role)
    await _active_member(pid, uid, role)
    before = (await client.get(f"/projects/{pid}", headers=_h(admin_token))).json()

    for change in _CHANGES:
        r = await client.patch(f"/projects/{pid}", headers=_h(tok), json=change)
        assert r.status_code == 403, f"{role} {change}: {r.status_code} {r.text}"
        assert r.json()["detail"] == "not_project_admin", f"{role} {change}: {r.text}"

    after = (await client.get(f"/projects/{pid}", headers=_h(admin_token))).json()
    for change in _CHANGES:
        (key,) = change
        assert after[key] == before[key], f"{role}가 {key}를 바꿨다"
    assert await _updated_events(pid) == []


async def test_project_admin_member_changes_general_settings(
    client: AsyncClient, admin_token: str,
):
    """멤버로 들어온 프로젝트 관리자가 같은 필드를 바꿀 수 있고 그 변경이 감사 기록에 남는지 확인한다."""
    pid = await new_project(client, admin_token, "gate-pa")
    pa_uid, pa = await add_member(client, admin_token, pid, "project_admin")
    await _active_member(pid, pa_uid, "project_admin")

    body: dict = {}
    for change in _CHANGES:
        body.update(change)
    r = await client.patch(f"/projects/{pid}", headers=_h(pa), json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    for key, val in body.items():
        assert got[key] == val, key

    [ev] = await _updated_events(pid)
    assert str(ev.actor_user_id) == pa_uid
    assert str(ev.target_id) == pid
    assert set(ev.payload["changed"]) == set(body)
