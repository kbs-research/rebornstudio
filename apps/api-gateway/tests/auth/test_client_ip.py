"""클라이언트 주소 판정 테스트. 신뢰하는 프록시가 넘긴 값만 믿는다.

로그인 한도와 감사 출처는 모두 `audit.client_ip` 하나를 읽는다.
프록시 해석은 서버 입구(uvicorn `--proxy-headers`와 `FORWARDED_ALLOW_IPS`)가 한 번만 한다.
두 규칙이 다르면 프록시(nginx) 뒤에서 플랫폼 전체가 한도 버킷 하나를 나눠 쓰거나, `X-Forwarded-For` 헤더 하나로 감사 출처를 위조할 수 있다.
보장하는 것은 세 가지다.

1. 신뢰하지 않는 곳에서 온 `X-Forwarded-For`는 무시한다. 위조한 헤더로 한도·출처를 바꿀 수 없다.
2. 신뢰하는 프록시가 넘긴 값은 반영한다. 사용자마다 버킷과 출처가 다르다.
3. 로그인 한도의 키와 감사 출처가 같은 주소다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.main import app
from api_gateway.models import DomainEvent
from api_gateway.services import audit
from api_gateway.services.auth import rate_limit
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette.requests import Request
from tests._helpers import make_account
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


def _request(client: tuple[str, int] | None, xff: str | None = None) -> Request:
    """헤더와 접속 주소만 있는 최소 요청을 만든다."""
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": client})


def test_spoofed_forwarded_for_is_ignored_without_a_trusted_proxy():
    """프록시를 거치지 않은 요청의 `X-Forwarded-For`를 무시하는지 확인한다.

    한도와 출처가 같은 주소를 쓴다.
    """
    req = _request(("198.51.100.7", 5555), xff="192.0.2.66")
    assert audit.client_ip(req) == "198.51.100.7"
    assert audit.origin(req)["ip"] == "198.51.100.7", "감사 출처를 헤더로 지어낼 수 없다"
    assert rate_limit._client_key(req) == "198.51.100.7", "한도를 헤더로 비켜 갈 수 없다"


def test_missing_client_is_none_not_a_guess():
    """주소 정보가 없으면 추측하지 않고 None을 돌려주는지 확인한다. 한도는 한 키("unknown")로 모은다."""
    req = _request(None)
    assert audit.client_ip(req) is None
    assert rate_limit._client_key(req) == "unknown"


async def _login_ip(client: AsyncClient, uid: str, email: str, pwd: str, xff: str) -> str | None:
    """로그인을 한 번 하고 그 사용자의 가장 최근 `auth.login` 감사 출처 주소를 읽는다."""
    r = await client.post("/auth/login", json={"email": email, "password": pwd},
                          headers={"X-Forwarded-For": xff})
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        ev = (await s.scalars(
            select(DomainEvent).where(
                DomainEvent.event_type == "auth.login",
                DomainEvent.actor_user_id == UUID(uid))
            .order_by(DomainEvent.occurred_at.desc()).limit(1)
        )).first()
    return (ev.payload or {}).get("ip") if ev else None


async def test_trusted_proxy_forwarded_for_reaches_audit_and_limit(admin_token: str):
    """신뢰하는 프록시(여기서는 127.0.0.1)가 넘긴 주소가 감사 출처에 원래 클라이언트 주소로 반영되는지 확인한다.

    운영과 같은 구조로 감싼다.
    uvicorn의 `ProxyHeadersMiddleware`가 입구에서 `client`를 바꾸고, 앱은 그 결과만 읽는다.
    그러면 로그인 이력의 출처가 nginx가 아니라 사용자가 된다.
    """
    edge = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    transport = ASGITransport(app=edge, client=("127.0.0.1", 40000))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        uid, email, pwd, _tok = await make_account(c, admin_token, "xffok")
        # 오른쪽부터 보아 신뢰하지 않는 첫 주소를 쓴다. 사용자가 앞에 끼워 넣은 값은 쓰지 않는다.
        assert await _login_ip(c, uid, email, pwd, "192.0.2.66, 203.0.113.9") == "203.0.113.9"

    # 신뢰하지 않는 곳(외부 주소)에서 온 같은 헤더는 무시된다.
    transport = ASGITransport(app=edge, client=("198.51.100.20", 40001))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert await _login_ip(c, uid, email, pwd, "203.0.113.9") == "198.51.100.20"
