"""로그아웃이 요청에 실린 access 토큰도 폐기하는지 보는 테스트.

refresh 토큰만 폐기하면 access 토큰은 만료(기본 60분)까지 계속 통한다.
공용 PC에서 로그아웃한 뒤에도 남은 토큰으로 API를 부를 수 있다.
"""
from httpx import AsyncClient
from tests._helpers import SAME_ORIGIN, make_user


async def test_logout_revokes_bearer_access_token(client: AsyncClient, admin_token: str):
    """Authorization 헤더와 함께 로그아웃하면 그 access 토큰이 바로 401인지 확인한다."""
    _, tok = await make_user(client, admin_token, "logoutacc")
    hdr = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/auth/me", headers=hdr)).status_code == 200
    r = await client.post("/auth/logout", headers={**hdr, **SAME_ORIGIN}, json={})
    assert r.status_code == 204, r.text
    assert (await client.get("/auth/me", headers=hdr)).status_code == 401


async def test_logout_ignores_bad_bearer(client: AsyncClient):
    """서명이 맞지 않는 Bearer 값이 와도 로그아웃이 204로 끝나는지 확인한다."""
    r = await client.post(
        "/auth/logout", headers={"Authorization": "Bearer garbage", **SAME_ORIGIN}, json={},
    )
    assert r.status_code == 204, r.text
