"""refresh 세션 절대 수명을 보는 테스트.

로그인 시각(`auth_time`)은 교체를 거쳐도 그대로 옮겨지고, 그 시각에서 `jwt_session_max_days`가
지나면 refresh가 401이다. 교체를 반복해도 세션이 늘어나지 않아야 탈취한 refresh 토큰으로
세션을 무기한 잇지 못한다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest_asyncio
from api_gateway import security
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import User
from api_gateway.services.auth import login_throttle
from httpx import AsyncClient
from tests._helpers import make_account, refresh_cookie_of, refresh_headers, refresh_with_cookie


@pytest_asyncio.fixture(autouse=True)
def _reset_throttle():
    """로그인 실패 잠금을 테스트마다 비운다."""
    login_throttle.reset()
    yield
    login_throttle.reset()


def _claims(token: str) -> dict:
    """서명을 검증하고 refresh 토큰의 payload를 돌려준다."""
    return jwt.decode(
        token, settings.jwt_refresh_secret, algorithms=["HS256"],
        audience="rebornstudio-refresh", issuer="rebornstudio-api",
    )


def _access_claims(token: str) -> dict:
    """만료를 보지 않고 access 토큰의 payload를 돌려준다."""
    return jwt.decode(
        token, settings.jwt_secret, algorithms=["HS256"],
        audience="rebornstudio-web", issuer="rebornstudio-api",
        options={"verify_exp": False},
    )


async def _account(client: AsyncClient, admin_token: str, prefix: str) -> tuple[str, str, str]:
    """활성 계정을 만들고 (user_id, 이메일, 비밀번호)를 돌려준다.

    계정은 재설정 링크로 비밀번호를 정하므로 비밀번호 변경 시각이 지금이다. 그 시각보다 앞선
    로그인을 흉내 내면 비밀번호 변경 가드가 먼저 막으므로 변경 시각을 지운다.
    """
    uid, email, pwd, _tok = await make_account(client, admin_token, prefix)
    async with SessionLocal() as s:
        user = await s.get(User, UUID(uid))
        user.password_changed_at = None
        await s.commit()
    return uid, email, pwd


def _at(monkeypatch, when: datetime) -> None:
    """토큰 발급 시각을 `when`으로 고정한다."""
    monkeypatch.setattr(security, "_now", lambda: when)


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    """이메일로 로그인하고 세션을 돌려준다."""
    r = await client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {**r.json()["session"], "refresh_token": refresh_cookie_of(r)}


async def _refresh(client: AsyncClient, token: str):
    """refresh 쿠키를 실어 refresh 요청을 보낸다."""
    return await refresh_with_cookie(client, token)


async def _refresh_body(client: AsyncClient, token: str):
    """쿠키 없이 본문 refresh 토큰으로 refresh 요청을 보낸다. 쿠키로 옮기기 전 브라우저의 호환 경로다."""
    return await client.post("/auth/refresh", headers=refresh_headers(None), json={"refresh_token": token})


async def test_rotation_keeps_login_time_and_never_extends(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """교체를 두 번 해도 로그인 시각이 그대로이고 두 토큰의 만료가 로그인 시각과 절대 수명의 합을 넘지 않는지 확인한다."""
    _uid, email, pwd = await _account(client, admin_token, "maxage")
    login_at = datetime.now(UTC) - timedelta(days=settings.jwt_session_max_days) + timedelta(hours=1)
    _at(monkeypatch, login_at)
    sess = await _login(client, email, pwd)
    monkeypatch.undo()

    end = int(login_at.timestamp()) + settings.jwt_session_max_days * 86400
    token = sess["refresh_token"]
    for _ in range(2):
        r = await _refresh(client, token)
        assert r.status_code == 200, r.text
        token = refresh_cookie_of(r)
        claims = _claims(token)
        assert claims["auth_time"] == int(login_at.timestamp())
        assert claims["exp"] <= end, "교체가 세션을 늘렸다"
        access = _access_claims(r.json()["access_token"])
        assert access["exp"] <= end, "access 토큰이 세션 끝을 넘어 유효하다"


async def test_refresh_after_session_max_is_rejected(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """로그인 뒤 절대 수명이 지나면 토큰 자체의 만료가 남아 있어도 refresh가 401인지 확인한다."""
    _uid, email, pwd = await _account(client, admin_token, "maxage2")
    _at(monkeypatch, datetime.now(UTC) - timedelta(days=10))
    sess = await _login(client, email, pwd)
    monkeypatch.undo()
    # 토큰을 발급한 뒤 운영자가 절대 수명을 줄인 경우다. 토큰의 exp는 아직 남아 있다.
    monkeypatch.setattr(settings, "jwt_session_max_days", 7)
    r = await _refresh(client, sess["refresh_token"])
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_refresh_token"


async def test_session_within_max_still_refreshes(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """절대 수명 안의 세션은 로그인한 지 오래됐어도 계속 갱신되는지 확인한다."""
    _uid, email, pwd = await _account(client, admin_token, "maxage3")
    _at(monkeypatch, datetime.now(UTC) - timedelta(days=6))
    sess = await _login(client, email, pwd)
    monkeypatch.undo()
    monkeypatch.setattr(settings, "jwt_session_max_days", 7)
    assert (await _refresh(client, sess["refresh_token"])).status_code == 200


def _legacy_refresh(uid: str, issued: datetime, expires: datetime) -> str:
    """`auth_time` 클레임이 없는 기존 형식의 refresh 토큰을 만든다."""
    return jwt.encode({
        "sub": uid, "iss": "rebornstudio-api", "aud": "rebornstudio-refresh",
        "iat": int(issued.timestamp()), "exp": int(expires.timestamp()),
        "type": "refresh", "jti": uuid4().hex, "fam": uuid4().hex,
    }, settings.jwt_refresh_secret, algorithm="HS256")


async def test_legacy_token_starts_at_its_issue_time(client: AsyncClient, admin_token: str):
    """클레임이 없는 기존 토큰은 발급 시각을 로그인 시각으로 보고 교체 뒤 그 시각을 옮기는지 확인한다."""
    uid, _email, _pwd = await _account(client, admin_token, "maxage4")
    now = datetime.now(UTC)
    issued = now - timedelta(days=1)
    r = await _refresh_body(client, _legacy_refresh(uid, issued, now + timedelta(days=13)))
    assert r.status_code == 200, r.text
    assert _claims(refresh_cookie_of(r))["auth_time"] == int(issued.timestamp())


async def test_legacy_token_past_max_is_rejected(client: AsyncClient, admin_token: str):
    """클레임이 없는 기존 토큰도 발급 시각에서 절대 수명이 지났으면 401인지 확인한다."""
    uid, _email, _pwd = await _account(client, admin_token, "maxage5")
    now = datetime.now(UTC)
    issued = now - timedelta(days=settings.jwt_session_max_days + 1)
    r = await _refresh_body(client, _legacy_refresh(uid, issued, now + timedelta(days=1)))
    assert r.status_code == 401
