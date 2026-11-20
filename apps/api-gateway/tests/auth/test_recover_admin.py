"""서버 명령으로 최고관리자의 재설정 링크를 발급하는 경로(`api_gateway.cli.recover_admin`)를 확인한다."""
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from api_gateway.cli import recover_admin
from api_gateway.database import SessionLocal
from api_gateway.models import PasswordResetToken, User
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import events, make_user
from tests.conftest import uniq


def _h(token: str) -> dict:
    """Bearer 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _super_admin(client: AsyncClient, admin_token: str) -> tuple[str, str]:
    """활성 최고관리자를 만들고 (uid, email)을 돌려준다."""
    email = uniq("recover")
    r = await client.post("/admin/users", headers=_h(admin_token),
                          json={"email": email, "display_name": "복구 대상",
                                "global_role": "super_admin"})
    assert r.status_code == 201, r.text
    return r.json()["user"]["user_id"], email


async def test_link_resets_password_without_issuer(client: AsyncClient, admin_token: str, capsys):
    """발급한 링크로 비밀번호를 바꿔 로그인하고, 발급자 없이 서버 명령 발급으로 기록되는지 확인한다.

    이메일은 대소문자를 구분하지 않는다. 링크는 표준 출력에만 나온다.
    """
    uid, email = await _super_admin(client, admin_token)
    code = await recover_admin.recover(email.upper(), "https://studio.example.com/")
    assert code == recover_admin.EXIT_OK
    out = capsys.readouterr().out
    link = next(line for line in out.splitlines() if line.startswith("링크: "))[len("링크: "):]
    # 토큰은 주소 조각에 싣는다. 조각은 브라우저가 서버로 보내지 않아 접근 로그에 남지 않는다.
    assert link.startswith("https://studio.example.com/password-reset#token=")
    assert urlparse(link).query == ""
    token = parse_qs(urlparse(link).fragment)["token"][0]

    pwd = "Recovered-Pass-123!"
    ok = await client.post("/auth/password/reset/confirm",
                           json={"token": token, "new_password": pwd})
    assert ok.status_code == 204, ok.text
    login = await client.post("/auth/login", json={"email": email, "password": pwd})
    assert login.status_code == 200, login.text

    async with SessionLocal() as s:
        row = await s.scalar(
            select(PasswordResetToken).where(PasswordResetToken.user_id == UUID(uid)))
    assert row is not None and row.issued_by is None
    ev = await events(event_type="user.password_reset_issued", target_id=uid)
    assert ev[0]["actor_user_id"] is None
    assert ev[0]["payload"]["via"] == "server_command"


async def test_refuses_non_super_admin_and_unknown(client: AsyncClient, admin_token: str, capsys):
    """일반 사용자와 없는 이메일에는 발급하지 않는다. 일반 사용자는 화면에서 발급받는다."""
    uid, _ = await make_user(client, admin_token, "plain")
    async with SessionLocal() as s:
        email = (await s.get(User, UUID(uid))).email

    assert await recover_admin.recover(email, None) == recover_admin.EXIT_NO_TARGET
    assert "최고관리자 계정이 아닙니다" in capsys.readouterr().err
    assert await recover_admin.recover(uniq("nobody"), None) == recover_admin.EXIT_NO_TARGET
    assert "계정이 없습니다" in capsys.readouterr().err
    # 계정을 만들 때 화면 발급 기록이 하나 있다. 서버 명령 발급 기록은 없어야 한다.
    ev = await events(event_type="user.password_reset_issued", target_id=uid)
    assert not [e for e in ev if e["payload"].get("via") == "server_command"]


async def test_needs_email_when_several_admins(client: AsyncClient, admin_token: str, capsys):
    """활성 최고관리자가 여럿이면 이메일 없이 고르지 않고 목록을 보인다."""
    _, email = await _super_admin(client, admin_token)
    assert await recover_admin.recover(None, None) == recover_admin.EXIT_NO_TARGET
    captured = capsys.readouterr()
    assert email in captured.err
    assert "링크:" not in captured.out
