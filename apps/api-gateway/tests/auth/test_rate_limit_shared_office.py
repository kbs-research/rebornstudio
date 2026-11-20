"""한 공인 IP 뒤에서 여러 작업자가 일하는 사무실 조건에서 인증 한도를 보는 테스트.

테스트 클라이언트의 요청은 모두 같은 주소에서 온다. 그래서 사무실 하나의 공인 IP와 같은 조건이다.
성공한 로그인과 유효한 쿠키의 refresh는 IP 한도를 쓰지 않고, 실패만 IP로 센다.
한 세션의 갱신은 세션 단위로, 비밀번호 변경 실패는 사용자 단위로 센다.
"""
from datetime import UTC, datetime
from uuid import UUID

import pytest_asyncio
from api_gateway import security
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import User
from api_gateway.services.auth import login_throttle, rate_limit
from httpx import AsyncClient
from tests._helpers import refresh_cookie_of, refresh_with_cookie
from tests.conftest import uniq

_PW = "OfficeShared123!@"
_OFFICE = 50  # 한 사무실 IP 뒤의 작업자 수


@pytest_asyncio.fixture(autouse=True)
def _limits_on(monkeypatch):
    """한도를 켜고 상태를 비운다. test 환경은 한도를 건너뛰므로 local로 바꾼다."""
    monkeypatch.setattr(settings, "environment", "local")
    login_throttle.reset()
    rate_limit.reset_for_test()
    yield
    login_throttle.reset()
    rate_limit.reset_for_test()


async def _active_users(n: int, *, password: str | None = None) -> list[tuple[UUID, str]]:
    """활성 계정 n개를 DB에 바로 만들고 (id, 이메일) 목록을 돌려준다. 비밀번호 해시는 한 번만 만든다."""
    hashed = security.hash_password(password) if password else None
    now = datetime.now(UTC)
    users = [
        User(email=uniq("office"), display_name="사무실", approval_status="active",
             password_hash=hashed, privacy_consented_at=now)
        for _ in range(n)
    ]
    async with SessionLocal() as s:
        s.add_all(users)
        await s.commit()
        return [(u.user_id, u.email) for u in users]


def _refresh_token(user_id: UUID) -> str:
    """새 로그인 세션의 refresh 토큰을 만든다."""
    return security.create_refresh_token(user_id, auth_time=security.new_auth_time())


async def test_office_of_fifty_refreshes_without_429(client: AsyncClient):
    """같은 IP의 서로 다른 사용자 50명이 각자 세 번씩 갱신해도 429가 나지 않는지 확인한다."""
    users = await _active_users(_OFFICE)
    cookies = {uid: _refresh_token(uid) for uid, _ in users}
    codes = []
    for _ in range(3):
        for uid in cookies:
            r = await refresh_with_cookie(client, cookies[uid])
            codes.append(r.status_code)
            cookies[uid] = refresh_cookie_of(r) or cookies[uid]
    assert codes.count(200) == 3 * _OFFICE, sorted(set(codes))


async def test_invalid_cookie_burst_hits_ip_limit_but_valid_session_continues(
    client: AsyncClient, monkeypatch,
):
    """무효 쿠키 연타는 IP 한도에 걸려 429와 Retry-After를 받고, 같은 IP의 유효한 세션은 계속 갱신되는지 확인한다."""
    monkeypatch.setattr(settings, "rate_limit_refresh_per_min", 5)
    codes = [(await refresh_with_cookie(client, "forged")).status_code for _ in range(5)]
    assert codes == [401] * 5
    r = await refresh_with_cookie(client, None)
    assert r.status_code == 429, r.text
    assert r.json()["detail"].startswith("rate_limit_exceeded:refresh ")
    assert 0 < int(r.headers["Retry-After"]) <= 60

    [(uid, _)] = await _active_users(1)
    r = await refresh_with_cookie(client, _refresh_token(uid))
    assert r.status_code == 200, r.text


async def test_one_session_refresh_burst_hits_session_limit(client: AsyncClient, monkeypatch):
    """한 세션의 갱신 연타는 세션 한도에 걸리고 같은 IP의 다른 세션은 갱신되는지 확인한다."""
    monkeypatch.setattr(settings, "rate_limit_refresh_session_per_min", 3)
    (uid, _), (other, _) = await _active_users(2)
    cookie = _refresh_token(uid)
    for _ in range(3):
        r = await refresh_with_cookie(client, cookie)
        assert r.status_code == 200, r.text
        cookie = refresh_cookie_of(r)
    r = await refresh_with_cookie(client, cookie)
    assert r.status_code == 429, r.text
    assert r.json()["detail"].startswith("rate_limit_exceeded:refresh_session ")
    assert int(r.headers["Retry-After"]) > 0
    # 한도에 걸린 요청은 쿠키를 교체하지 않는다. 창이 끝나면 같은 쿠키로 이어 간다.
    assert refresh_cookie_of(r) is None

    r = await refresh_with_cookie(client, _refresh_token(other))
    assert r.status_code == 200, r.text


async def test_office_of_fifty_logs_in_without_429(client: AsyncClient):
    """같은 IP에서 50명이 로그인에 성공해도 429가 나지 않는지 확인한다(IP 실패 한도 분당 10)."""
    users = await _active_users(_OFFICE, password=_PW)
    codes = [
        (await client.post("/auth/login", json={"email": e, "password": _PW})).status_code
        for _, e in users
    ]
    assert codes == [200] * _OFFICE


async def test_failed_login_burst_hits_ip_limit(client: AsyncClient, monkeypatch):
    """같은 IP의 실패 로그인 연타는 한도에 걸리고, 그 뒤에는 맞는 비밀번호도 429인지 확인한다.

    한도를 넘은 IP에 성공 응답을 주면 대입 공격이 비밀번호를 확인할 수 있다.
    """
    monkeypatch.setattr(settings, "rate_limit_auth_per_min", 3)
    [(_, email)] = await _active_users(1, password=_PW)
    codes = [
        (await client.post(
            "/auth/login", json={"email": uniq("spray"), "password": "Wrong123!@abcd"},
        )).status_code
        for _ in range(3)
    ]
    assert codes == [401, 401, 401]
    r = await client.post("/auth/login", json={"email": email, "password": _PW})
    assert r.status_code == 429, r.text
    assert r.json()["detail"].startswith("rate_limit_exceeded:auth:login ")
    assert int(r.headers["Retry-After"]) > 0


async def test_email_lock_unchanged(client: AsyncClient):
    """이메일 단위 잠금은 그대로라서 같은 이메일 5회 실패 뒤 맞는 비밀번호도 too_many_attempts인지 확인한다."""
    [(_, email)] = await _active_users(1, password=_PW)
    for _ in range(5):
        r = await client.post("/auth/login", json={"email": email, "password": "Wrong123!@abcd"})
        assert r.status_code == 401, r.text
    r = await client.post("/auth/login", json={"email": email, "password": _PW})
    assert r.status_code == 429, r.text
    assert r.json()["detail"] == "too_many_attempts"


async def test_password_change_limit_is_per_user(client: AsyncClient, monkeypatch):
    """비밀번호 변경 실패는 사용자 단위로 세어 같은 IP의 다른 사람을 막지 않는지 확인한다."""
    monkeypatch.setattr(settings, "rate_limit_auth_per_min", 2)
    (a, _), (b, _) = await _active_users(2, password=_PW)
    body = {"old_password": "Wrong123!@abcd", "new_password": "BrandNew123!@#x"}

    def h(uid: UUID) -> dict:
        """그 사용자의 access 토큰 헤더."""
        token, _ = security.create_access_token(uid, "user")
        return {"Authorization": f"Bearer {token}"}

    codes_a = [
        (await client.post("/auth/password/change", headers=h(a), json=body)).status_code
        for _ in range(3)
    ]
    assert codes_a == [401, 401, 429]
    r = await client.post("/auth/password/change", headers=h(b), json=body)
    assert r.status_code == 401, r.text


async def test_reset_check_counts_unusable_links(client: AsyncClient, monkeypatch):
    """쓸 수 없는 재설정 링크 확인을 IP별 실패로 세어 한도를 넘으면 429인지 확인한다."""
    monkeypatch.setattr(settings, "rate_limit_auth_per_min", 2)
    codes = [
        (await client.post("/auth/password/reset/check", json={"token": f"guess-{i}"})).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]


async def test_limiter_blocked_and_add_report_wait():
    """실패만 세는 두 동작(`blocked`·`add`)이 한도에 닿은 뒤에만 남은 초를 돌려주는지 확인한다."""
    lim = rate_limit.SlidingWindowLimiter(max_per_min=2, name="t")
    assert await lim.blocked("k") is None
    assert await lim.add("k") is None
    assert await lim.add("k") is None
    wait = await lim.blocked("k")
    assert wait is not None and 0 < wait <= 61
    assert await lim.add("k") is not None
