"""인증 보안 테스트. 로그인 잠금, 비밀번호 변경, 관리자 발급 재설정을 확인한다.

이메일 인증은 없다. 메일 발송 기능이 없어 인증을 요구할 곳도 메일을 보낼 곳도 없다.
비밀번호 재설정은 최고관리자가 발급하는 1회용 링크 하나로 한다. 자가 요청 경로는 없다.
"""
import pytest_asyncio
from api_gateway.services.auth import login_throttle
from httpx import AsyncClient
from tests._helpers import make_account
from tests.conftest import uniq


@pytest_asyncio.fixture(autouse=True)
def _reset_throttle():
    """각 테스트 전후에 로그인 잠금 상태를 초기화한다. 전역 상태를 테스트마다 격리한다."""
    login_throttle.reset()
    yield
    login_throttle.reset()


async def _issue_reset(client: AsyncClient, admin_token: str, uid: str) -> dict:
    """최고관리자가 재설정 링크를 발급한다. 재설정 토큰을 받는 유일한 경로다."""
    r = await client.post(
        f"/admin/users/{uid}/password-reset",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_email_verify_endpoint_is_gone(client: AsyncClient):
    """`/auth/email/verify`가 없는지(404) 확인한다."""
    r = await client.post("/auth/email/verify", json={"token": "garbage"})
    assert r.status_code == 404


async def test_self_service_reset_request_is_gone(client: AsyncClient):
    """자가 재설정 요청 경로가 없는지 확인한다. 경로가 있으면 메일을 보내지 않으면서 보냈다고 응답하게 된다."""
    r = await client.post("/auth/password/reset/request", json={"email": uniq("ghost")})
    assert r.status_code == 404


async def test_login_lockout_after_failures(client: AsyncClient, admin_token: str):
    """로그인에 5회 실패하면 429와 Retry-After를 돌려주는지 확인한다."""
    _uid, email, _pwd, _tok = await make_account(client, admin_token, "lockme")
    for _ in range(5):
        r = await client.post("/auth/login", json={"email": email, "password": "WrongPw1!@#xy"})
        assert r.status_code == 401
    # 6번째 시도는 잠금이다
    r = await client.post("/auth/login", json={"email": email, "password": "WrongPw1!@#xy"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


async def test_admin_issue_reset_then_confirm(client: AsyncClient, admin_token: str):
    """관리자가 발급한 링크로 새 비밀번호를 정하고 그 비밀번호로 로그인할 수 있는지 확인한다."""
    from tests._helpers import make_user

    uid, _ = await make_user(client, admin_token, "adminreset")
    issued = await _issue_reset(client, admin_token, uid)
    assert issued["valid_minutes"] == 30  # 재설정 링크의 유효 시간
    assert issued["token"]

    new_pw = "NewPw456!@#xyz"
    conf = await client.post(
        "/auth/password/reset/confirm",
        json={"token": issued["token"], "new_password": new_pw},
    )
    assert conf.status_code == 204
    r = await client.post("/auth/login", json={"email": issued["email"], "password": new_pw})
    assert r.status_code == 200
    assert r.json()["session"] is not None


async def test_issue_reset_records_audit_without_token(client: AsyncClient, admin_token: str):
    """재설정 링크 발급이 감사 로그에 남고 토큰 값은 남지 않는지 확인한다."""
    from tests._helpers import make_user

    uid, _ = await make_user(client, admin_token, "resetaudit")
    issued = await _issue_reset(client, admin_token, uid)
    from tests._helpers import events
    rows = await events(event_type="user.password_reset_issued", target_id=uid)
    # 계정을 만들 때 한 번, 이 테스트가 한 번 발급했다.
    assert len(rows) == 2
    assert all(r["payload"]["valid_minutes"] == 30 for r in rows)
    assert issued["token"] not in str(rows)


async def test_issue_reset_requires_super_admin(client: AsyncClient, admin_token: str):
    """재설정 링크 발급을 최고관리자만 호출할 수 있는지 확인한다. 다른 사람 계정의 비밀번호를 바꾸게 하는 액션이다."""
    from tests._helpers import make_user

    uid, tok = await make_user(client, admin_token, "notadmin")
    r = await client.post(
        f"/admin/users/{uid}/password-reset", headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 403


async def test_issue_reset_rejects_inactive_user(client: AsyncClient, admin_token: str):
    """정지한 계정에는 링크를 발급하지 않고 409 `user_not_active`를 돌려주는지 확인한다. 링크를 써도 로그인할 수 없다."""
    uid, _email, _pwd, _tok = await make_account(client, admin_token, "suspreset")
    hdr = {"Authorization": f"Bearer {admin_token}"}
    r = await client.patch(f"/admin/users/{uid}/status", headers=hdr, json={"status": "suspended"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/admin/users/{uid}/password-reset", headers=hdr)
    assert r.status_code == 409 and r.json()["detail"] == "user_not_active"


async def test_password_change_requires_old(client: AsyncClient, admin_token: str):
    """비밀번호 변경이 현재 비밀번호를 확인하는지 본다.

    틀리면 401이고 맞으면 204다. 이 테스트는 틀린 경우의 401만 확인한다.
    """
    bad = await client.post(
        "/auth/password/change",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"old_password": "definitely-wrong", "new_password": "BrandNew123!@#x"},
    )
    assert bad.status_code == 401


async def test_reset_token_single_use(client: AsyncClient, admin_token: str):
    """재설정 토큰이 1회용인지 확인한다. confirm 뒤 같은 토큰을 다시 쓰면 400이다.

    서버가 confirm할 때 토큰 행을 지운다.
    """
    from tests._helpers import make_user

    uid, _ = await make_user(client, admin_token, "single")
    token = (await _issue_reset(client, admin_token, uid))["token"]
    first = await client.post(
        "/auth/password/reset/confirm", json={"token": token, "new_password": "NewPw456!@#xyz"})
    assert first.status_code == 204
    again = await client.post(
        "/auth/password/reset/confirm", json={"token": token, "new_password": "Other789!@#xyz"})
    assert again.status_code == 400


async def test_reissue_invalidates_previous_link(client: AsyncClient, admin_token: str):
    """재발급하면 이전 링크가 무효가 되는지 확인한다. 사용자당 한 행이라 새 발급이 덮어쓴다."""
    from tests._helpers import make_user

    uid, _ = await make_user(client, admin_token, "reissue")
    first = (await _issue_reset(client, admin_token, uid))["token"]
    second = (await _issue_reset(client, admin_token, uid))["token"]
    assert first != second
    stale = await client.post(
        "/auth/password/reset/confirm", json={"token": first, "new_password": "NewPw456!@#xyz"})
    assert stale.status_code == 400
    fresh = await client.post(
        "/auth/password/reset/confirm", json={"token": second, "new_password": "NewPw456!@#xyz"})
    assert fresh.status_code == 204


async def test_expired_link_is_refused(client: AsyncClient, admin_token: str):
    """만료된 링크가 400인지 확인한다. 유효 시간은 토큰이 아니라 서버의 행에 저장된다.

    30분을 기다릴 수 없으므로 발급된 행의 `expires_at`을 과거로 옮겨 만료 검사만 재현한다.
    """
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import PasswordResetToken
    from tests._helpers import make_user

    uid, _ = await make_user(client, admin_token, "expired")
    token = (await _issue_reset(client, admin_token, uid))["token"]
    async with SessionLocal() as s:
        row = await s.get(PasswordResetToken, UUID(uid))
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    r = await client.post(
        "/auth/password/reset/confirm", json={"token": token, "new_password": "NewPw456!@#xyz"})
    assert r.status_code == 400


async def test_confirm_rejects_unknown_token(client: AsyncClient):
    """알 수 없는 토큰이 400인지 확인한다. 해시로 조회하므로 맞는 행이 없다."""
    r = await client.post(
        "/auth/password/reset/confirm", json={"token": "garbage", "new_password": "NewPw456!@#xyz"})
    assert r.status_code == 400


async def test_reset_check_reports_state_without_consuming(client: AsyncClient, admin_token: str):
    """링크 상태 확인이 유효 여부·만료 시각·유효 시간을 주고 토큰을 소비하지 않는지 확인한다.

    화면이 새 비밀번호를 받기 전에 호출하므로, 확인한 뒤에도 같은 링크로 재설정할 수 있어야 한다.
    쓸 수 없는 링크는 `valid=False`와 이유(`reason`)를 준다. 토큰 없이 요청하면 이유는 없다.
    """
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import PasswordResetToken
    from tests._helpers import make_user

    blank = await client.post("/auth/password/reset/check", json={})
    assert blank.status_code == 200
    assert blank.json() == {"valid": False, "expires_at": None, "valid_minutes": 30, "reason": None}
    unknown = await client.post("/auth/password/reset/check", json={"token": "garbage"})
    assert unknown.json()["valid"] is False

    uid, _ = await make_user(client, admin_token, "check")
    issued = await _issue_reset(client, admin_token, uid)
    ok = (await client.post("/auth/password/reset/check", json={"token": issued["token"]})).json()
    assert ok["valid"] is True and ok["valid_minutes"] == 30
    assert ok["expires_at"] and ok["expires_at"][:16] == issued["expires_at"][:16]
    # 확인은 토큰을 소비하지 않는다. 같은 링크로 재설정할 수 있다.
    again = (await client.post("/auth/password/reset/check", json={"token": issued["token"]})).json()
    assert again["valid"] is True

    async with SessionLocal() as s:
        row = await s.get(PasswordResetToken, UUID(uid))
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    gone = (await client.post("/auth/password/reset/check", json={"token": issued["token"]})).json()
    assert gone == {"valid": False, "expires_at": None, "valid_minutes": 30, "reason": "expired"}


async def test_password_change_supersedes_old_session(client: AsyncClient, admin_token: str):
    """password_changed_at보다 먼저 발급된 토큰이 무효인지 확인한다(`get_current_user` 가드).

    토큰 iat는 초 단위라서 같은 초 안의 경계를 피해야 한다.
    그래서 password_changed_at을 DB에 미래 시각으로 넣어 가드 로직을 결정론적으로 검증한다.
    운영에서는 비밀번호 변경이 토큰 발급보다 1초 이상 뒤에 일어나므로 정상 동작한다.
    """
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import User
    from tests._helpers import make_user

    uid, tok = await make_user(client, admin_token, "supersede")
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})).status_code == 200
    async with SessionLocal() as s:
        u = await s.get(User, UUID(uid))
        u.password_changed_at = datetime.now(UTC) + timedelta(seconds=60)
        await s.commit()
    after = await client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert after.status_code == 401  # 변경 전에 발급한 토큰은 무효가 된다


async def test_password_change_supersedes_old_refresh(client: AsyncClient, admin_token: str):
    """password_changed_at 뒤에는 기존 refresh token도 401인지 확인한다.

    refresh로 가드를 우회하는 경로를 막는다.
    """
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import User
    from tests._helpers import make_user

    uid, _tok = await make_user(client, admin_token, "refresh-bypass")
    # 로그인해서 refresh token을 얻는다.
    # 비밀번호를 모르므로 관리자 발급 링크로 새로 정한 뒤 로그인한다.
    issued = await _issue_reset(client, admin_token, uid)
    email = issued["email"]
    await client.post(
        "/auth/password/reset/confirm",
        json={"token": issued["token"], "new_password": "Login123!@#xyz"},
    )
    login = await client.post("/auth/login", json={"email": email, "password": "Login123!@#xyz"})
    from tests._helpers import refresh_cookie_of, refresh_with_cookie
    refresh_token = refresh_cookie_of(login)
    # refresh 호출 전에 password_changed_at을 미래 시각으로 넣는다.
    # revoke가 아니라 이 가드만으로 막히는지 확인한다.
    async with SessionLocal() as s:
        u = await s.get(User, UUID(uid))
        u.password_changed_at = datetime.now(UTC) + timedelta(seconds=60)
        await s.commit()
    # 이 refresh token은 rotation·revoke된 적이 없다.
    # 그러므로 401은 password_changed_at 가드 때문이다.
    blocked = await refresh_with_cookie(client, refresh_token)
    assert blocked.status_code == 401
