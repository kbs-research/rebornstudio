"""사용자 관리의 계정 상태 경계를 보는 테스트.

계정 상태는 `active`와 `suspended` 두 가지다. 공개 가입과 승인 절차가 없으므로 다른 값은 받지 않는다.
보장하는 것은 세 가지다.

1. 상태 변경(`PATCH /admin/users/{id}/status`)은 두 값만 받고 나머지는 422다.
2. 사용자 목록의 `status` 필터도 두 값만 받고 나머지는 422 `invalid_status`다.
3. 정지한 계정은 맞는 비밀번호로 로그인해도 403 `account_suspended`이고, 다시 활성화하면 로그인할 수 있다.
"""
import pytest
from httpx import AsyncClient
from tests._helpers import make_account


def _h(token: str) -> dict[str, str]:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("bad", ["pending", "rejected", "deleted"])
async def test_status_change_accepts_only_active_or_suspended(
    client: AsyncClient, admin_token: str, bad: str,
):
    """상태 변경이 active·suspended 밖의 값을 422로 거절하고 계정 상태를 바꾸지 않는지 확인한다."""
    uid, _email, _pwd, _tok = await make_account(client, admin_token, "stbad")
    r = await client.patch(f"/admin/users/{uid}/status", headers=_h(admin_token), json={"status": bad})
    assert r.status_code == 422, r.text
    rows = (await client.get("/users", headers=_h(admin_token))).json()
    me = next(u for u in rows if u["user_id"] == uid)
    assert me["approval_status"] == "active"


async def test_user_list_status_filter_accepts_only_known_values(
    client: AsyncClient, admin_token: str,
):
    """사용자 목록의 `status` 필터가 active·suspended만 받고 나머지는 422 `invalid_status`인지 확인한다."""
    for ok in ("active", "suspended"):
        r = await client.get("/users", headers=_h(admin_token), params={"status": ok})
        assert r.status_code == 200, r.text
        assert all(u["approval_status"] == ok for u in r.json())
    for bad in ("pending", "rejected"):
        r = await client.get("/users", headers=_h(admin_token), params={"status": bad})
        assert r.status_code == 422 and r.json()["detail"] == "invalid_status", r.text


async def test_suspended_account_login_is_refused_until_reactivated(
    client: AsyncClient, admin_token: str,
):
    """정지한 계정의 로그인이 403 `account_suspended`이고 다시 활성화하면 200인지 확인한다."""
    uid, email, pwd, _tok = await make_account(client, admin_token, "stsus")
    r = await client.patch(
        f"/admin/users/{uid}/status", headers=_h(admin_token), json={"status": "suspended"},
    )
    assert r.status_code == 200 and r.json()["approval_status"] == "suspended", r.text

    blocked = await client.post("/auth/login", json={"email": email, "password": pwd})
    assert blocked.status_code == 403 and blocked.json()["detail"] == "account_suspended"

    r = await client.patch(
        f"/admin/users/{uid}/status", headers=_h(admin_token), json={"status": "active"},
    )
    assert r.status_code == 200, r.text
    again = await client.post("/auth/login", json={"email": email, "password": pwd})
    assert again.status_code == 200 and again.json()["session"]["access_token"]
