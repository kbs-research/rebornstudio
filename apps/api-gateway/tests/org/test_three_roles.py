"""역할이 플랫폼 둘(최고관리자·일반 사용자)과 프로젝트 셋(관리자·검수자·라벨러)뿐인지 보는 테스트.

없어진 역할(staff·operator·viewer·rights_reviewer·legal_reviewer)은 API가 422로 거절하고,
API를 거치지 않는 쓰기도 DB 제약(`ck_user_global_role`·`ck_member_role`)이 막는다.
API만 막으면 시드·스크립트가 넣은 행이 권한 판정에 없는 역할로 남는다.
"""
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import ProjectMember, User
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from tests._helpers import add_member, make_user, new_project

#: 없어진 프로젝트 역할.
_REMOVED_PROJECT_ROLES = ("operator", "viewer", "rights_reviewer", "legal_reviewer")


def _h(tok: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.parametrize("role", ["viewer", "operator"])
async def test_member_role_constraint_rejects_removed_roles(
    client: AsyncClient, admin_token: str, role: str,
):
    """멤버십 역할을 없어진 값으로 직접 바꾸면 DB 제약이 IntegrityError로 거절하는지 확인한다."""
    pid = await new_project(client, admin_token, f"ck-{role[:4]}")
    uid, _ = await add_member(client, admin_token, pid, "annotator")
    async with SessionLocal() as s:
        with pytest.raises(IntegrityError):
            await s.execute(
                update(ProjectMember)
                .where(ProjectMember.project_id == UUID(pid), ProjectMember.user_id == UUID(uid))
                .values(role=role)
            )
            await s.flush()
        await s.rollback()


async def test_global_role_constraint_rejects_staff(client: AsyncClient, admin_token: str):
    """플랫폼 역할을 staff로 직접 바꾸면 DB 제약이 IntegrityError로 거절하는지 확인한다."""
    uid, _ = await make_user(client, admin_token, "ckstaff")
    async with SessionLocal() as s:
        with pytest.raises(IntegrityError):
            await s.execute(
                update(User).where(User.user_id == UUID(uid)).values(global_role="staff")
            )
            await s.flush()
        await s.rollback()
    async with SessionLocal() as s:
        got = await s.scalar(select(User.global_role).where(User.user_id == UUID(uid)))
    assert got == "user"


@pytest.mark.parametrize("role", _REMOVED_PROJECT_ROLES)
async def test_invite_rejects_removed_roles(client: AsyncClient, admin_token: str, role: str):
    """없어진 프로젝트 역할로 초대하면 422이고 멤버십 행이 생기지 않는지 확인한다."""
    pid = await new_project(client, admin_token, f"inv-{role[:4]}")
    uid, _ = await make_user(client, admin_token, "invrm")
    async with SessionLocal() as s:
        email = await s.scalar(select(User.email).where(User.user_id == UUID(uid)))
    r = await client.post(
        f"/projects/{pid}/members/invite", headers=_h(admin_token),
        json={"email": email, "role": role},
    )
    assert r.status_code == 422, r.text
    async with SessionLocal() as s:
        row = await s.get(ProjectMember, (UUID(pid), UUID(uid)))
    assert row is None


@pytest.mark.parametrize("role", _REMOVED_PROJECT_ROLES)
async def test_role_change_rejects_removed_roles(
    client: AsyncClient, admin_token: str, role: str,
):
    """멤버 역할을 없어진 값으로 바꾸려 하면 422이고 역할이 그대로인지 확인한다."""
    pid = await new_project(client, admin_token, f"chg-{role[:4]}")
    uid, _ = await add_member(client, admin_token, pid, "annotator")
    r = await client.patch(
        f"/projects/{pid}/members/{uid}/role", headers=_h(admin_token), json={"role": role},
    )
    assert r.status_code == 422, r.text
    async with SessionLocal() as s:
        row = await s.get(ProjectMember, (UUID(pid), UUID(uid)))
    assert row.role == "annotator"


async def test_three_project_roles_are_accepted(client: AsyncClient, admin_token: str):
    """남은 세 프로젝트 역할로는 초대와 역할 변경이 모두 되는지 확인한다."""
    pid = await new_project(client, admin_token, "three-ok")
    uid, _ = await add_member(client, admin_token, pid, "annotator")
    for role in ("reviewer", "project_admin", "annotator"):
        r = await client.patch(
            f"/projects/{pid}/members/{uid}/role", headers=_h(admin_token), json={"role": role},
        )
        assert r.status_code == 200 and r.json()["role"] == role, r.text
