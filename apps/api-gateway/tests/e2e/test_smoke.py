"""헬스 체크와 주요 흐름을 하나씩 확인하는 스모크 테스트."""
from httpx import AsyncClient
from tests._helpers import make_account, make_user
from tests.conftest import uniq


async def test_healthz(client: AsyncClient):
    """healthz는 ok를 돌려주고 X-Request-Id를 발급한다."""
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    # 미들웨어가 X-Request-Id를 발급한다
    assert r.headers.get("x-request-id")


async def test_readyz(client: AsyncClient):
    """readyz는 ready를 돌려준다."""
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


async def test_request_id_propagation(client: AsyncClient):
    """요청에 준 X-Request-Id가 응답에 그대로 돌아온다."""
    r = await client.get("/healthz", headers={"X-Request-Id": "test-rid-1234"})
    assert r.headers["x-request-id"] == "test-rid-1234"


async def test_admin_login(client: AsyncClient, admin_token: str):
    """admin 토큰이 JWT 형태로 발급되는지 확인한다."""
    assert len(admin_token) > 20  # JWT 형태


async def test_admin_me(client: AsyncClient, admin_token: str):
    """admin 토큰으로 /auth/me를 조회하면 super_admin 정보를 돌려준다."""
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "admin@example.com"
    assert r.json()["global_role"] == "super_admin"


async def test_created_account_logs_in_with_a_session(client: AsyncClient, admin_token: str):
    """관리자가 만든 계정이 정한 비밀번호로 세션을 받고, 틀린 비밀번호는 401인지 확인한다."""
    _uid, email, pwd, _tok = await make_account(client, admin_token, "smoke")
    r = await client.post("/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"user", "session"}
    assert body["session"]["access_token"] and "refresh_token" not in body["session"]
    assert r.cookies.get("reborn_refresh"), "refresh 토큰은 HttpOnly 쿠키로 온다"

    # 비밀번호가 틀리면 없는 계정과 같은 401을 돌려준다.
    r = await client.post("/auth/login", json={"email": email, "password": "Wrong123!@a!"})
    assert r.status_code == 401


async def test_password_policy_violation(client: AsyncClient, admin_token: str):
    """비밀번호 정책을 어긴 새 비밀번호를 재설정 확정에서 422로 거절한다."""
    uid, _ = await make_user(client, admin_token, "weak")
    issued = (await client.post(
        f"/admin/users/{uid}/password-reset", headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    r = await client.post(
        "/auth/password/reset/confirm", json={"token": issued["token"], "new_password": "short"},
    )
    assert r.status_code == 422  # Field min_length 위반


async def test_public_signup_and_oauth_are_gone(client: AsyncClient):
    """공개 가입·외부 로그인·가입 승인 경로가 없는지 확인한다. 계정은 관리자 생성으로만 생긴다."""
    r = await client.post(
        "/auth/email/signup",
        json={"email": uniq("gone"), "password": "GoneTest123!@", "display_name": "X"},
    )
    assert r.status_code in (404, 405), r.text
    paths = (await client.get("/openapi.json")).json()["paths"]
    removed = ("/auth/oauth", "/auth/email/signup", "/admin/users/pending")
    gone = [
        p for p in paths
        if p.startswith(removed)
        or (p.startswith("/admin/users") and p.endswith(("/approve", "/reject")))
    ]
    assert gone == [], gone


async def test_admin_users_list(client: AsyncClient, admin_token: str):
    """admin은 사용자 목록을 조회할 수 있다."""
    r = await client.get("/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert any(u["email"] == "admin@example.com" for u in r.json())


async def test_openapi_endpoint(client: AsyncClient):
    """openapi.json이 OpenAPI 3 스펙과 주요 경로를 돌려준다."""
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"].startswith("3.")
    assert "/auth/login" in spec["paths"]
    assert "/work/me" in spec["paths"]


# ── super_admin이 role과 status를 바꾼다 ──


async def test_set_role_changes_role(client: AsyncClient, admin_token: str):
    """다른 사용자의 global_role을 super_admin으로 바꾼다."""
    uid, _ = await make_user(client, admin_token, "fg8r")
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.patch(f"/admin/users/{uid}/role", json={"role": "super_admin"}, headers=h)
    assert r.status_code == 200
    assert r.json()["global_role"] == "super_admin"


async def test_set_role_self_403(client: AsyncClient, admin_token: str):
    """자기 role 변경을 403 cannot_change_own_role로 거절한다."""
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    admin_id = r.json()["user_id"]
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.patch(f"/admin/users/{admin_id}/role", json={"role": "user"}, headers=h)
    assert r.status_code == 403
    assert r.json()["detail"] == "cannot_change_own_role"


async def test_set_role_invalid_422(client: AsyncClient, admin_token: str):
    """유효하지 않은 role을 422로 거절한다."""
    uid, _ = await make_user(client, admin_token, "fg8ri")
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.patch(f"/admin/users/{uid}/role", json={"role": "god"}, headers=h)
    assert r.status_code == 422


async def test_set_role_staff_422(client: AsyncClient, admin_token: str):
    """플랫폼 역할 목록에 없는 staff를 422로 거절하고 역할을 그대로 두는지 확인한다."""
    uid, tok = await make_user(client, admin_token, "fg8rs")
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.patch(f"/admin/users/{uid}/role", json={"role": "staff"}, headers=h)
    assert r.status_code == 422
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.json()["global_role"] == "user"


async def test_set_status_suspend_then_active(client: AsyncClient, admin_token: str):
    """사용자를 suspended로 바꾼 뒤 다시 active로 바꾼다."""
    uid, _ = await make_user(client, admin_token, "fg8s")
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.patch(f"/admin/users/{uid}/status", json={"status": "suspended"}, headers=h)
    assert r.status_code == 200
    assert r.json()["approval_status"] == "suspended"
    r = await client.patch(f"/admin/users/{uid}/status", json={"status": "active"}, headers=h)
    assert r.status_code == 200
    assert r.json()["approval_status"] == "active"


async def test_set_status_self_suspend_403(client: AsyncClient, admin_token: str):
    """자기 자신의 suspended 변경을 403 cannot_suspend_self로 거절한다."""
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    admin_id = r.json()["user_id"]
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.patch(f"/admin/users/{admin_id}/status", json={"status": "suspended"}, headers=h)
    assert r.status_code == 403
    assert r.json()["detail"] == "cannot_suspend_self"

