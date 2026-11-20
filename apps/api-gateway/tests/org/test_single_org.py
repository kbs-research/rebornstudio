"""조직이 기본 조직 한 행뿐인지 보는 테스트.

RebornStudio는 조직 관리 화면과 API가 없다. 프로젝트와 자격증명 연결은 모두 기본 조직에 속한다.
조직이 두 행이 되면 서버가 고르는 기본 조직이 실행마다 달라질 수 있으므로 DB 인덱스로 막는다.
"""
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Organization, Project
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from tests._helpers import default_org_id
from tests.conftest import uniq


async def test_second_organization_row_is_rejected():
    """조직 행을 하나 더 넣으면 유일 인덱스 위반으로 거절되는지 확인한다."""
    async with SessionLocal() as s:
        s.add(Organization(slug=uniq("org").split("@")[0], name=uniq("둘째 조직").split("@")[0]))
        with pytest.raises(IntegrityError, match="uq_organizations_single"):
            await s.commit()
        await s.rollback()
    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(Organization)) == 1


async def test_organizations_route_is_gone(client: AsyncClient, admin_token: str):
    """조직 관리 경로가 없어 최고관리자에게도 404인지 확인한다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    assert (await client.get("/organizations", headers=h)).status_code == 404
    r = await client.post("/organizations", headers=h, json={"slug": "second", "name": "둘째"})
    assert r.status_code == 404


async def test_project_create_lands_in_default_org(client: AsyncClient, admin_token: str):
    """조직을 보내지 않고 만든 프로젝트가 기본 조직에 속하고 응답에 조직 필드가 없는지 확인한다."""
    slug = uniq("single").split("@")[0]
    r = await client.post("/projects", headers={"Authorization": f"Bearer {admin_token}"},
                          json={"slug": slug, "name": slug})
    assert r.status_code == 201, r.text
    assert "org_id" not in r.json()
    async with SessionLocal() as s:
        org_id = await s.scalar(select(Project.org_id).where(
            Project.project_id == UUID(r.json()["project_id"])))
    assert str(org_id) == await default_org_id()
