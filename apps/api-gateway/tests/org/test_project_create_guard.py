"""POST /projects 권한 가드 테스트. super_admin만 프로젝트를 만들 수 있다."""
from httpx import AsyncClient
from tests._helpers import make_user
from tests.conftest import uniq


async def test_regular_user_cannot_create_project(client: AsyncClient, admin_token: str):
    """일반 사용자(global_role=user)가 POST /projects를 부르면 403인지 확인한다."""
    _, user_tok = await make_user(client, admin_token, "guard")

    slug = uniq("g").split("@")[0]
    r = await client.post(
        "/projects",
        headers={"Authorization": f"Bearer {user_tok}"},
        json={"slug": slug, "name": "should-fail"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "not_authorized"


async def test_super_admin_can_create_project(client: AsyncClient, admin_token: str):
    """super_admin이 프로젝트를 만들 수 있는지(201) 확인하는 회귀 테스트다."""
    slug = uniq("ok").split("@")[0]
    r = await client.post(
        "/projects",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"slug": slug, "name": "admin-ok"},
    )
    assert r.status_code == 201

