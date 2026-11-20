"""관리자 계정 생성(POST /admin/users)을 확인한다. 공개 가입이 없으므로 계정은 이 경로로만 생긴다."""
from httpx import AsyncClient
from tests._helpers import events, make_user
from tests.conftest import uniq


def _h(token: str) -> dict:
    """Bearer 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def test_created_account_sets_password_by_link_and_needs_consent(
    client: AsyncClient, admin_token: str,
):
    """만든 계정은 활성이고, 재설정 링크로 비밀번호를 정해 로그인하며, 동의 전에는 업무 API가 막힌다."""
    email = uniq("created")
    r = await client.post("/admin/users", headers=_h(admin_token),
                          json={"email": email, "display_name": "새 사람"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["approval_status"] == "active"
    assert body["user"]["global_role"] == "user"
    assert body["user"]["privacy_consented_at"] is None
    assert body["reset"]["user_id"] == body["user"]["user_id"]

    pwd = "Created-Pass-123!"
    ok = await client.post("/auth/password/reset/confirm",
                           json={"token": body["reset"]["token"], "new_password": pwd})
    assert ok.status_code == 204, ok.text
    tok = (await client.post("/auth/login", json={"email": email, "password": pwd})
           ).json()["session"]["access_token"]
    blocked = await client.get("/projects", headers=_h(tok))
    assert blocked.status_code == 403 and blocked.json()["detail"] == "privacy_consent_required"

    ev = await events(event_type="user.created", target_id=body["user"]["user_id"])
    assert [e["payload"]["email"] for e in ev] == [email]


async def test_duplicate_email_is_rejected_ignoring_case(client: AsyncClient, admin_token: str):
    """같은 이메일(대소문자 무시)의 계정이 있으면 409 `email_in_use`다."""
    email = uniq("dup")
    first = await client.post("/admin/users", headers=_h(admin_token),
                              json={"email": email, "display_name": "a"})
    assert first.status_code == 201
    again = await client.post("/admin/users", headers=_h(admin_token),
                              json={"email": email.upper(), "display_name": "b"})
    assert again.status_code == 409 and again.json()["detail"] == "email_in_use"


async def test_only_super_admin_can_create(client: AsyncClient, admin_token: str):
    """최고관리자가 아니면 계정을 만들 수 없다."""
    _, tok = await make_user(client, admin_token, "notsuper")
    r = await client.post("/admin/users", headers=_h(tok),
                          json={"email": uniq("x"), "display_name": "x"})
    assert r.status_code == 403
