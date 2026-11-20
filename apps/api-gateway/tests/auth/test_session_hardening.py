"""로그아웃이 끊는 세션의 범위를 보는 테스트.

로그아웃이 요청에 실린 토큰의 jti만 폐기하면, 교체로 갈라진 같은 로그인의 다른 refresh 토큰이 살아남는다.
탈취한 쪽이 먼저 교체한 경우 사용자가 로그아웃해도 탈취한 세션은 그대로 남는다.
"""
import pytest_asyncio
from api_gateway.services.auth import login_throttle
from httpx import AsyncClient
from tests._helpers import make_account, refresh_cookie_of, refresh_headers, refresh_with_cookie


@pytest_asyncio.fixture(autouse=True)
def _reset_throttle():
    """로그인 실패 잠금을 테스트마다 비운다."""
    login_throttle.reset()
    yield
    login_throttle.reset()


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    """로그인하고 세션 본문에 쿠키의 refresh 토큰을 더해 돌려준다."""
    r = await client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {**r.json()["session"], "refresh_token": refresh_cookie_of(r)}


async def test_logout_ends_the_rotated_branch_of_the_same_login(
    client: AsyncClient, admin_token: str,
):
    """교체되기 전 refresh 토큰으로 로그아웃해도 같은 로그인의 새 토큰이 401인지 확인한다."""
    _uid, email, pwd, _tok = await make_account(client, admin_token, "harden")
    sess = await _login(client, email, pwd)
    rotated = await refresh_with_cookie(client, sess["refresh_token"])
    assert rotated.status_code == 200, rotated.text
    branch = {**rotated.json(), "refresh_token": refresh_cookie_of(rotated)}

    r = await client.post("/auth/logout", headers=refresh_headers(sess["refresh_token"]))
    assert r.status_code == 204, r.text

    again = await refresh_with_cookie(client, branch["refresh_token"])
    assert again.status_code == 401, "로그아웃 뒤에도 같은 로그인의 갈라진 refresh 토큰이 교체된다"
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {branch['access_token']}"},
    )
    assert me.status_code == 401, "로그아웃 뒤에도 같은 로그인의 access 토큰이 통한다"


async def test_logout_keeps_other_logins_alive(client: AsyncClient, admin_token: str):
    """한 로그인을 로그아웃해도 같은 계정의 다른 로그인 세션은 유지되는지 확인한다."""
    _uid, email, pwd, _tok = await make_account(client, admin_token, "harden2")
    first = await _login(client, email, pwd)
    second = await _login(client, email, pwd)

    r = await client.post("/auth/logout", headers=refresh_headers(first["refresh_token"]))
    assert r.status_code == 204, r.text

    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert me.status_code == 200, me.text
