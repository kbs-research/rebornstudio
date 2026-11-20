"""refresh 토큰을 HttpOnly 쿠키로 주고받는 경로를 보는 테스트.

로그인과 refresh는 refresh 토큰을 본문에 싣지 않고 쿠키로 준다. refresh·logout은 쿠키에서
토큰을 읽고, 허용 출처가 아닌 요청은 403으로 거절한다. 브라우저 저장소에 refresh 토큰이 남은 사용자는
본문 토큰을 한 번 보내 쿠키로 바꾼다. 쿠키의 `Secure`는 요청이 https로 왔을 때만 붙는다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest_asyncio
from api_gateway import security
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.main import app
from api_gateway.models import User
from api_gateway.services.auth import login_throttle
from httpx import ASGITransport, AsyncClient
from tests._helpers import (
    REFRESH_COOKIE,
    SAME_ORIGIN,
    make_account,
    refresh_cookie_of,
    refresh_headers,
    refresh_with_cookie,
)


@pytest_asyncio.fixture(autouse=True)
def _reset_throttle():
    """로그인 실패 잠금을 테스트마다 비운다."""
    login_throttle.reset()
    yield
    login_throttle.reset()


def _set_cookie_line(response) -> str:
    """응답의 refresh 쿠키 `Set-Cookie` 줄을 돌려준다. 없으면 실패한다."""
    lines = [
        v for k, v in response.headers.multi_items()
        if k.lower() == "set-cookie" and v.startswith(f"{REFRESH_COOKIE}=")
    ]
    assert len(lines) == 1, response.headers
    return lines[0]


def _attrs(line: str) -> dict[str, str]:
    """`Set-Cookie` 줄의 속성을 소문자 키 사전으로 바꾼다. 값 없는 속성은 빈 문자열이다."""
    out = {}
    for part in line.split(";")[1:]:
        k, _, v = part.strip().partition("=")
        out[k.lower()] = v
    return out


def _claims(token: str) -> dict:
    """서명을 검증하고 refresh 토큰의 payload를 돌려준다."""
    return jwt.decode(
        token, settings.jwt_refresh_secret, algorithms=["HS256"],
        audience="rebornstudio-refresh", issuer="rebornstudio-api",
    )


async def _account(client: AsyncClient, admin_token: str, prefix: str = "cookie") -> tuple[str, str, str]:
    """활성 계정을 만들고 (user_id, 이메일, 비밀번호)를 돌려준다.

    계정은 재설정 링크로 비밀번호를 정해 비밀번호 변경 시각이 지금이다. 로그인 시각을 앞당기는 테스트가
    비밀번호 변경 가드에 먼저 막히지 않도록 변경 시각을 지운다.
    """
    uid, email, pwd, _tok = await make_account(client, admin_token, prefix)
    async with SessionLocal() as s:
        user = await s.get(User, UUID(uid))
        user.password_changed_at = None
        await s.commit()
    return uid, email, pwd


async def _login(client: AsyncClient, email: str, password: str):
    """이메일로 로그인한 응답을 돌려준다."""
    r = await client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r


async def _login_token(client: AsyncClient, admin_token: str) -> str:
    """새 계정으로 로그인하고 쿠키의 refresh 토큰을 돌려준다."""
    _uid, email, pwd = await _account(client, admin_token)
    return refresh_cookie_of(await _login(client, email, pwd))


def _assert_session_cookie(response) -> str:
    """응답이 HttpOnly·SameSite=Strict·Path=/api/auth 쿠키를 주고 본문에 refresh 토큰이 없는지 본다.

    Returns:
        쿠키에 실린 refresh 토큰.
    """
    attrs = _attrs(_set_cookie_line(response))
    assert "httponly" in attrs
    assert attrs["samesite"].lower() == "strict"
    assert attrs["path"] == "/api/auth"
    assert "refresh_token" not in response.text
    token = refresh_cookie_of(response)
    assert token
    end = _claims(token)["auth_time"] + settings.jwt_session_max_days * 86400
    now = int(datetime.now(UTC).timestamp())
    assert 0 < int(attrs["max-age"]) <= end - now + 1, "쿠키 수명이 세션 끝을 넘는다"
    return token


async def test_login_sets_httponly_cookie_and_hides_refresh(
    client: AsyncClient, admin_token: str,
):
    """로그인이 refresh 토큰을 쿠키로만 주고 본문에는 access 토큰만 싣는지 확인한다."""
    _uid, email, pwd = await _account(client, admin_token)
    r = await _login(client, email, pwd)
    _assert_session_cookie(r)
    assert set(r.json()["session"]) == {"access_token", "access_expires_at"}


async def test_plain_http_cookie_is_not_secure(client: AsyncClient, admin_token: str):
    """평문 http 요청에는 `Secure`를 붙이지 않는지 확인한다. 붙이면 브라우저가 저장하지 않아 새로고침마다 로그인이 풀린다."""
    _uid, email, pwd = await _account(client, admin_token)
    r = await _login(client, email, pwd)
    assert "secure" not in _attrs(_set_cookie_line(r))
    rotated = await refresh_with_cookie(client, refresh_cookie_of(r))
    assert rotated.status_code == 200, rotated.text
    assert "secure" not in _attrs(_set_cookie_line(rotated))


async def test_https_request_gets_secure_cookie_and_own_origin(
    client: AsyncClient, admin_token: str,
):
    """https로 온 요청에는 `Secure`를 붙이고, 자기 출처를 https로 보아 같은 출처의 refresh를 받는지 확인한다.

    운영에서는 uvicorn이 신뢰 프록시의 `X-Forwarded-Proto`를 요청 scheme에 반영한다.
    테스트는 그 결과와 같은 scheme(https)의 요청을 바로 보낸다.
    """
    _uid, email, pwd = await _account(client, admin_token)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as tls:
        login = await tls.post("/auth/login", json={"email": email, "password": pwd})
        assert login.status_code == 200, login.text
        assert "secure" in _attrs(_set_cookie_line(login))
        token = refresh_cookie_of(login)
        r = await tls.post(
            "/auth/refresh",
            headers={"Origin": "https://test", "Cookie": f"{REFRESH_COOKIE}={token}"},
        )
        assert r.status_code == 200, r.text
        assert "secure" in _attrs(_set_cookie_line(r))
        cross = await tls.post(
            "/auth/refresh",
            headers={"Origin": "http://test", "Cookie": f"{REFRESH_COOKIE}={refresh_cookie_of(r)}"},
        )
        assert cross.status_code == 403, "https 서버가 http 출처의 refresh를 받는다"


async def test_forwarded_proto_header_alone_does_not_change_scheme(
    client: AsyncClient, admin_token: str,
):
    """신뢰 프록시를 거치지 않은 `X-Forwarded-Proto`만으로는 쿠키에 `Secure`가 붙지 않는지 확인한다.

    헤더를 반영하는 것은 uvicorn의 프록시 처리이고, 그 처리는 신뢰 대역에서 온 요청에만 적용된다.
    """
    _uid, email, pwd = await _account(client, admin_token)
    r = await client.post(
        "/auth/login", headers={"X-Forwarded-Proto": "https"},
        json={"email": email, "password": pwd},
    )
    assert r.status_code == 200, r.text
    assert "secure" not in _attrs(_set_cookie_line(r))


async def test_refresh_reads_cookie_and_rotates_it(client: AsyncClient, admin_token: str):
    """refresh가 쿠키로 동작하고 교체한 새 쿠키를 주며, 새 쿠키로 다시 갱신되는지 확인한다."""
    first = await _login_token(client, admin_token)
    r = await refresh_with_cookie(client, first)
    assert r.status_code == 200, r.text
    second = _assert_session_cookie(r)
    assert second != first
    assert set(r.json()) == {"access_token", "access_expires_at"}
    assert _claims(second)["auth_time"] == _claims(first)["auth_time"]
    again = await refresh_with_cookie(client, second)
    assert again.status_code == 200, again.text


async def test_rotated_cookie_keeps_session_max(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """교체한 쿠키의 수명이 로그인 시각과 절대 수명의 합을 넘지 않고, 그 뒤에는 401인지 확인한다."""
    _uid, email, pwd = await _account(client, admin_token)
    login_at = datetime.now(UTC) - timedelta(days=settings.jwt_session_max_days) + timedelta(hours=1)
    monkeypatch.setattr(security, "_now", lambda: login_at)
    token = refresh_cookie_of(await _login(client, email, pwd))
    monkeypatch.undo()

    r = await refresh_with_cookie(client, token)
    assert r.status_code == 200, r.text
    max_age = int(_attrs(_set_cookie_line(r))["max-age"])
    assert max_age <= 3600, "교체가 쿠키 수명을 세션 끝 너머로 늘렸다"

    monkeypatch.setattr(settings, "jwt_session_max_days", 7)
    assert (await refresh_with_cookie(client, refresh_cookie_of(r))).status_code == 401


async def test_refresh_without_any_token_is_401(client: AsyncClient):
    """쿠키도 본문 토큰도 없으면 401 `invalid_refresh_token`인지 확인한다."""
    r = await client.post("/auth/refresh", headers=SAME_ORIGIN)
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_refresh_token"


async def test_refresh_from_other_origin_is_403(client: AsyncClient, admin_token: str):
    """다른 출처의 refresh는 403이고 쿠키의 토큰은 그대로 쓸 수 있는지 확인한다."""
    token = await _login_token(client, admin_token)
    for headers in (
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Referer": "https://evil.example/page"},
        {},
    ):
        r = await client.post(
            "/auth/refresh", headers={**headers, "Cookie": f"{REFRESH_COOKIE}={token}"},
        )
        assert r.status_code == 403, (headers, r.text)
        assert r.json()["detail"] == "origin_not_allowed"
    assert (await refresh_with_cookie(client, token)).status_code == 200


async def test_refresh_accepts_referer_and_cors_origin(client: AsyncClient, admin_token: str):
    """Origin이 없으면 같은 출처의 Referer를 받고, 설정한 CORS 출처도 받는지 확인한다."""
    token = await _login_token(client, admin_token)
    r = await client.post(
        "/auth/refresh",
        headers={"Referer": "http://test/p/x/work", "Cookie": f"{REFRESH_COOKIE}={token}"},
    )
    assert r.status_code == 200, r.text
    cors = settings.cors_origins_list[0]
    r2 = await client.post(
        "/auth/refresh",
        headers={"Origin": cors, "Cookie": f"{REFRESH_COOKIE}={refresh_cookie_of(r)}"},
    )
    assert r2.status_code == 200, r2.text


async def test_logout_clears_cookie_and_revokes_family(client: AsyncClient, admin_token: str):
    """로그아웃이 쿠키 삭제를 싣고, 같은 로그인의 교체된 토큰과 access 토큰을 모두 끊는지 확인한다."""
    first = await _login_token(client, admin_token)
    rotated = await refresh_with_cookie(client, first)
    access = rotated.json()["access_token"]

    r = await client.post(
        "/auth/logout",
        headers={**refresh_headers(refresh_cookie_of(rotated)), "Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 204, r.text
    attrs = _attrs(_set_cookie_line(r))
    assert attrs["max-age"] == "0"
    assert attrs["path"] == "/api/auth"
    assert (await refresh_with_cookie(client, refresh_cookie_of(rotated))).status_code == 401
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 401


async def test_logout_from_other_origin_is_403(client: AsyncClient, admin_token: str):
    """다른 출처의 로그아웃은 403이고 세션이 그대로 이어지는지 확인한다."""
    token = await _login_token(client, admin_token)
    r = await client.post(
        "/auth/logout",
        headers={"Origin": "https://evil.example", "Cookie": f"{REFRESH_COOKIE}={token}"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "origin_not_allowed"
    assert (await refresh_with_cookie(client, token)).status_code == 200


async def test_body_token_is_exchanged_for_cookie_once(client: AsyncClient, admin_token: str):
    """쿠키 없이 본문 refresh 토큰을 보내면 한 번 교체해 쿠키로 주고, 그 뒤로는 쿠키로 이어지는지 확인한다."""
    uid, _email, _pwd = await _account(client, admin_token)
    legacy = security.create_refresh_token(UUID(uid))

    r = await client.post("/auth/refresh", headers=SAME_ORIGIN, json={"refresh_token": legacy})
    assert r.status_code == 200, r.text
    cookie = _assert_session_cookie(r)
    assert (await client.post(
        "/auth/refresh", headers=SAME_ORIGIN, json={"refresh_token": legacy},
    )).status_code == 401, "교환한 본문 토큰을 다시 쓸 수 있다"
    assert (await refresh_with_cookie(client, cookie)).status_code == 200


async def test_cookie_wins_over_body_token(client: AsyncClient, admin_token: str):
    """쿠키와 본문 토큰이 함께 오면 쿠키를 쓰는지 확인한다. 이전 버전 화면이 본문에 다른 값을 실어도 갱신된다."""
    token = await _login_token(client, admin_token)
    r = await client.post(
        "/auth/refresh", headers=refresh_headers(token), json={"refresh_token": "undefined"},
    )
    assert r.status_code == 200, r.text
