"""refresh 토큰 재사용 탐지와 동시 교체를 보는 테스트.

교체(rotation)로 폐기된 refresh 토큰이 다시 오면 그 토큰이 유출됐을 수 있다.
폐기된 지 유예 시간(`REFRESH_REUSE_GRACE_SECONDS`)이 지났으면 family 전체를 끊어
탈취한 쪽과 정당한 쪽의 세션을 모두 끝낸다.
유예 안이면 여러 탭이 같은 토큰으로 동시에 갱신한 경우라 401만 준다.
같은 토큰으로 온 동시 요청은 폐기를 먼저 기록한 한쪽만 새 세션을 받는다.
"""
import asyncio
from uuid import UUID

import pytest
import pytest_asyncio
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent
from api_gateway.services.auth import jwt_revocation, login_throttle
from api_gateway.services.auth import service as auth_service
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import make_account, refresh_cookie_of, refresh_with_cookie
from tests.conftest import uniq


@pytest_asyncio.fixture(autouse=True)
def _reset_throttle():
    """로그인 실패 잠금을 테스트마다 비운다."""
    login_throttle.reset()
    yield
    login_throttle.reset()


async def _login(client: AsyncClient, admin_token: str) -> tuple[str, dict]:
    """활성 계정을 만들고 새로 로그인해 (user_id, 세션)을 돌려준다."""
    uid, email, pwd, _tok = await make_account(client, admin_token, "reuse")
    r = await client.post("/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, r.text
    return uid, {**r.json()["session"], "refresh_token": refresh_cookie_of(r)}


async def _refresh(client: AsyncClient, token: str):
    """refresh 쿠키를 실어 refresh 요청을 보낸다."""
    return await refresh_with_cookie(client, token)


async def test_reuse_after_grace_revokes_the_family(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """유예가 지난 재사용이면 401이고 교체로 받은 새 토큰도 401이며 감사 이벤트가 남는지 확인한다."""
    monkeypatch.setattr(auth_service, "REFRESH_REUSE_GRACE_SECONDS", -1)
    uid, sess = await _login(client, admin_token)
    rotated = await _refresh(client, sess["refresh_token"])
    assert rotated.status_code == 200, rotated.text
    new = {**rotated.json(), "refresh_token": refresh_cookie_of(rotated)}

    reused = await _refresh(client, sess["refresh_token"])
    assert reused.status_code == 401
    assert (await _refresh(client, new["refresh_token"])).status_code == 401
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {new['access_token']}"})
    assert me.status_code == 401, "family를 끊었는데 새 access 토큰이 통한다"
    async with SessionLocal() as s:
        n = len((await s.scalars(select(DomainEvent.event_id).where(
            DomainEvent.event_type == "auth.refresh_reused",
            DomainEvent.target_id == UUID(uid),
        ))).all())
    assert n == 1


async def test_reuse_within_grace_keeps_the_family(client: AsyncClient, admin_token: str):
    """유예 안의 재사용은 401만 주고 교체로 받은 새 토큰은 계속 쓸 수 있는지 확인한다."""
    _, sess = await _login(client, admin_token)
    rotated = await _refresh(client, sess["refresh_token"])
    assert rotated.status_code == 200, rotated.text
    assert (await _refresh(client, sess["refresh_token"])).status_code == 401
    assert (await _refresh(client, refresh_cookie_of(rotated))).status_code == 200


async def test_concurrent_rotation_gives_one_session(client: AsyncClient, admin_token: str):
    """같은 refresh 토큰으로 동시에 두 번 갱신하면 하나만 200이고 다른 하나는 401인지 확인한다."""
    _, sess = await _login(client, admin_token)
    a, b = await asyncio.gather(
        _refresh(client, sess["refresh_token"]), _refresh(client, sess["refresh_token"]),
    )
    assert sorted([a.status_code, b.status_code]) == [200, 401]
    winner = a if a.status_code == 200 else b
    assert (await _refresh(client, refresh_cookie_of(winner))).status_code == 200


def test_lru_revoke_reports_first_time_only():
    """인메모리 백엔드의 폐기가 처음 한 번만 참이고 폐기 시각을 돌려주는지 확인한다."""
    backend = jwt_revocation.LRUBackend()
    assert backend.revoke_jti("j1", 60) is True
    assert backend.revoke_jti("j1", 60) is False
    assert backend.jti_revoked_at("j1") is not None
    assert backend.jti_revoked_at("j2") is None


@pytest.fixture(scope="module")
def redis_client():
    """실제 Redis에 연결한다. 연결할 수 없으면 Redis 테스트를 skip한다."""
    import redis
    try:
        c = redis.from_url(settings.redis_url, socket_timeout=2, decode_responses=True)
        c.ping()
    except Exception:
        pytest.skip(f"Redis 없음({settings.redis_url})")
    return c


def test_redis_revoke_is_set_nx(redis_client):
    """Redis 백엔드의 폐기가 SET NX로 처음 한 번만 참이고, 다른 레플리카가 같은 결과를 보는지 확인한다."""
    import redis
    a = jwt_revocation.RedisBackend(redis_client)
    b = jwt_revocation.RedisBackend(
        redis.from_url(settings.redis_url, socket_timeout=2, decode_responses=True),
    )
    jti = uniq("nx")
    try:
        assert a.revoke_jti(jti, 60) is True
        assert b.revoke_jti(jti, 60) is False, "두 레플리카가 모두 처음 폐기했다고 본다"
        at = b.jti_revoked_at(jti)
        assert at is not None and at > 1_000_000_000
        assert 0 < redis_client.ttl(f"revoked:jti:{jti}") <= 60
    finally:
        redis_client.delete(f"revoked:jti:{jti}")
