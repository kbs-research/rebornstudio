"""계정 생성·로그인·토큰 갱신·재설정 링크의 경계를 보는 테스트.

맞는 비밀번호로 들어온 정지 계정에는 상태를 알린다. 비밀번호를 아는 사람에게 상태를 숨길 이유가 없다.
틀린 비밀번호에는 계속 같은 401을 돌려준다.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest_asyncio
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import PasswordResetToken, User
from api_gateway.services.auth import login_throttle, rate_limit
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import SAME_ORIGIN, make_account, make_user
from tests.conftest import uniq


@pytest_asyncio.fixture(autouse=True)
def _reset_state():
    """로그인 실패 잠금과 요청 한도를 테스트마다 비운다."""
    login_throttle.reset()
    rate_limit.reset_for_test()
    yield
    login_throttle.reset()
    rate_limit.reset_for_test()


def _h(tok: str) -> dict:
    """Bearer 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def test_blocked_account_with_right_password_is_told_why(
    client: AsyncClient, admin_token: str,
):
    """맞는 비밀번호면 정지 상태를 403 코드로 알리고 틀린 비밀번호는 401인지 확인한다."""
    uid, email, pwd, _tok = await make_account(client, admin_token, "sus")
    r = await client.patch(
        f"/admin/users/{uid}/status", headers=_h(admin_token), json={"status": "suspended"},
    )
    assert r.status_code == 200, r.text

    r = await client.post("/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "account_suspended"
    wrong = await client.post("/auth/login", json={"email": email, "password": "Wrong123!@abcd"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "invalid_credentials"


async def test_created_account_name_follows_the_screen_rules(client: AsyncClient, admin_token: str):
    """계정 생성이 이름을 공백을 뗀 1~100자로만 받는지 확인한다."""
    for bad in ("   ", "가" * 101):
        r = await client.post(
            "/admin/users", headers=_h(admin_token),
            json={"email": uniq("bad"), "display_name": bad},
        )
        assert r.status_code == 422, (bad, r.text)
    email = uniq("trim")
    r = await client.post(
        "/admin/users", headers=_h(admin_token),
        json={"email": email, "display_name": "  앞뒤 공백  "},
    )
    assert r.status_code == 201, r.text
    async with SessionLocal() as s:
        name = await s.scalar(select(User.display_name).where(User.email == email))
    assert name == "앞뒤 공백"


async def test_refresh_is_rate_limited(client: AsyncClient, monkeypatch):
    """무효 토큰의 갱신 실패에는 IP별 분당 한도가 있고 한도를 넘으면 429와 Retry-After인지 확인한다."""
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "rate_limit_refresh_per_min", 3)
    replies = [
        await client.post("/auth/refresh", headers=SAME_ORIGIN, json={"refresh_token": "fake"})
        for _ in range(4)
    ]
    assert [r.status_code for r in replies] == [401, 401, 401, 429]
    assert int(replies[-1].headers["Retry-After"]) > 0


async def test_reset_check_tells_expired_used_and_unknown_apart(
    client: AsyncClient, admin_token: str,
):
    """재설정 링크 상태가 만료·사용·알 수 없음을 구분하고, 쓴 링크로는 다시 재설정할 수 없는지 확인한다."""
    url = "/auth/password/reset/check"
    uid, _ = await make_user(client, admin_token, "rsreason")
    issued = (await client.post(
        f"/admin/users/{uid}/password-reset", headers=_h(admin_token),
    )).json()
    ok = (await client.post(url, json={"token": issued["token"]})).json()
    assert ok["valid"] is True and ok["reason"] is None

    r = await client.post(
        "/auth/password/reset/confirm", json={"token": issued["token"], "new_password": "NewResetPw123!@"},
    )
    assert r.status_code == 204, r.text
    used = (await client.post(url, json={"token": issued["token"]})).json()
    assert used["valid"] is False and used["reason"] == "used"
    again = await client.post(
        "/auth/password/reset/confirm", json={"token": issued["token"], "new_password": "NewResetPw123!@x"},
    )
    assert again.status_code == 400 and again.json()["detail"] == "reset_link_used"

    unknown = (await client.post(url, json={"token": "garbage"})).json()
    assert unknown["valid"] is False and unknown["reason"] == "unknown"

    uid2, _ = await make_user(client, admin_token, "rsexp")
    issued2 = (await client.post(
        f"/admin/users/{uid2}/password-reset", headers=_h(admin_token),
    )).json()
    async with SessionLocal() as s:
        row = await s.get(PasswordResetToken, UUID(uid2))
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    for _ in range(2):
        expired = (await client.post(url, json={"token": issued2["token"]})).json()
        assert expired["valid"] is False and expired["reason"] == "expired"
        r = await client.post(
            "/auth/password/reset/confirm",
            json={"token": issued2["token"], "new_password": "NewResetPw123!@"},
        )
        assert r.status_code == 400 and r.json()["detail"] == "invalid_or_expired_token"


async def test_one_reset_link_changes_the_password_once(client: AsyncClient, admin_token: str):
    """같은 링크로 두 요청이 동시에 와도 비밀번호가 한 번만 바뀌는지 확인한다."""
    uid, _ = await make_user(client, admin_token, "rsrace")
    issued = (await client.post(
        f"/admin/users/{uid}/password-reset", headers=_h(admin_token),
    )).json()

    async def confirm(pw: str) -> int:
        """같은 토큰으로 재설정을 확정하고 상태 코드를 돌려준다."""
        r = await client.post(
            "/auth/password/reset/confirm", json={"token": issued["token"], "new_password": pw},
        )
        return r.status_code

    codes = sorted(await asyncio.gather(confirm("RaceResetPwA123!@"), confirm("RaceResetPwB123!@")))
    assert codes == [204, 400]
