"""비밀번호 변경이 현재 비밀번호를 무차별 대입으로 찾는 데 쓰이지 않는지 보는 테스트.

비밀번호 변경은 현재 비밀번호를 확인하는 두 번째 입구다. 로그인만 잠그면 access 토큰을 가진
사람이 이 경로로 현재 비밀번호를 끝없이 맞혀 볼 수 있다.
"""
import pytest_asyncio
from api_gateway.config import settings
from api_gateway.services.auth import login_throttle, rate_limit
from httpx import AsyncClient
from tests._helpers import make_account


@pytest_asyncio.fixture(autouse=True)
def _reset_state():
    """로그인 실패 잠금과 요청 한도를 테스트마다 비운다."""
    login_throttle.reset()
    rate_limit.reset_for_test()
    yield
    login_throttle.reset()
    rate_limit.reset_for_test()


async def _member(client: AsyncClient, admin_token: str) -> tuple[str, str]:
    """활성 계정을 만들고 (현재 비밀번호, access 토큰)을 돌려준다."""
    _uid, _email, pwd, tok = await make_account(client, admin_token, "pwchg")
    return pwd, tok


async def test_wrong_current_password_counts_toward_lock(client: AsyncClient, admin_token: str):
    """현재 비밀번호를 5회 틀리면 맞는 비밀번호로 바꾸려 해도 429이고 Retry-After가 있는지 확인한다."""
    pwd, tok = await _member(client, admin_token)
    h = {"Authorization": f"Bearer {tok}"}
    for _ in range(5):
        r = await client.post("/auth/password/change", headers=h, json={
            "old_password": "Wrong123!@abcd", "new_password": "BrandNew123!@#x",
        })
        assert r.status_code == 401, r.text
    r = await client.post("/auth/password/change", headers=h, json={
        "old_password": pwd, "new_password": "BrandNew123!@#x",
    })
    assert r.status_code == 429, r.text
    assert r.json()["detail"] == "too_many_attempts"
    assert int(r.headers["Retry-After"]) > 0


async def test_password_change_is_rate_limited(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """비밀번호 변경에도 사용자별 실패 한도가 있고 한도를 넘으면 429와 Retry-After인지 확인한다."""
    _, tok = await _member(client, admin_token)
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "rate_limit_auth_per_min", 2)
    rate_limit.reset_for_test()
    h = {"Authorization": f"Bearer {tok}"}
    body = {"old_password": "Wrong123!@abcd", "new_password": "BrandNew123!@#x"}
    codes = [
        (await client.post("/auth/password/change", headers=h, json=body)).status_code
        for _ in range(3)
    ]
    assert codes == [401, 401, 429]
    r = await client.post("/auth/password/change", headers=h, json=body)
    assert int(r.headers["Retry-After"]) > 0
