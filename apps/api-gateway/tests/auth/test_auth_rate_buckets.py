"""인증 경로의 IP별 실패 한도가 경로마다 따로 세어지고 Redis 카운터가 영구 차단을 남기지 않는지 보는 테스트.

한 사무실은 NAT 뒤에서 IP 하나를 같이 쓴다. 로그인·재설정 확인과 확정이 버킷 하나를 나눠 쓰면
로그인 실패가 몰린 것만으로 재설정 링크를 연 사람까지 429를 받는다.
INCR와 EXPIRE를 따로 보내면 그 사이에 연결이 끊긴 키는 TTL 없이 남아 그 IP를 영구히 막는다.
"""
import pytest
import pytest_asyncio
from api_gateway.config import settings
from api_gateway.services.auth import login_throttle, rate_limit
from api_gateway.services.auth.rate_limit import RedisFixedWindowLimiter
from httpx import AsyncClient
from tests.conftest import uniq


@pytest_asyncio.fixture(autouse=True)
def _reset_state():
    """로그인 실패 잠금과 요청 한도를 테스트마다 비운다."""
    login_throttle.reset()
    rate_limit.reset_for_test()
    yield
    login_throttle.reset()
    rate_limit.reset_for_test()


@pytest.fixture()
def redis_client():
    """실제 Redis에 연결한다. 연결할 수 없으면 skip한다."""
    import redis
    try:
        c = redis.from_url(settings.redis_url, socket_timeout=2, decode_responses=True)
        c.ping()
    except Exception:
        pytest.skip(f"Redis 없음({settings.redis_url})")
    return c


async def test_login_burst_does_not_block_reset_check(client: AsyncClient, monkeypatch):
    """로그인 실패 한도를 다 쓴 IP에서도 재설정 링크 확인과 확정이 각자의 한도로 도는지 확인한다."""
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "rate_limit_auth_per_min", 2)
    rate_limit.reset_for_test()
    for _ in range(2):
        r = await client.post(
            "/auth/login", json={"email": uniq("burst"), "password": "Whatever123!@a"},
        )
        assert r.status_code == 401, r.text
    r = await client.post(
        "/auth/login", json={"email": uniq("burst"), "password": "Whatever123!@a"},
    )
    assert r.status_code == 429
    r = await client.post("/auth/password/reset/check", json={"token": "nope"})
    assert r.status_code == 200, r.text
    r = await client.post(
        "/auth/password/reset/confirm", json={"token": "nope", "new_password": "Whatever123!@a"},
    )
    assert r.status_code == 400, r.text


async def test_rate_limit_key_always_carries_ttl(redis_client):
    """TTL 없이 남은 카운터 키도 다음 요청에서 TTL을 받아 영구 차단으로 남지 않는지 확인한다."""
    name = f"t-{uniq('ttl').split('@')[0]}"
    key = "10.0.0.9"
    rkey = f"rl:{name}:{key}"
    lim = RedisFixedWindowLimiter(max_per_min=100, name=name, client=redis_client)
    try:
        redis_client.set(rkey, 7)  # INCR 뒤 EXPIRE 전에 끊긴 상태
        await lim.hit(key)
        assert 0 < redis_client.ttl(rkey) <= 60
        redis_client.delete(rkey)
        await lim.hit(key)
        assert 0 < redis_client.ttl(rkey) <= 60
    finally:
        redis_client.delete(rkey)


def test_login_fail_counter_always_carries_ttl(redis_client):
    """TTL 없이 남은 로그인 실패 카운터도 다음 실패에서 보존 시간을 받는지 확인한다."""
    backend = login_throttle.RedisBackend(redis_client)
    email = uniq("failttl")
    fail_key = f"throttle:login:fail:{email}"
    try:
        redis_client.set(fail_key, 1)
        backend.record_failure(email)
        assert redis_client.ttl(fail_key) > 0
    finally:
        backend.clear(email)


async def test_redis_failure_counter_reports_wait(redis_client):
    """Redis 제한기의 실패 세기(`add`)와 막힘 확인(`blocked`)이 한도에 닿은 뒤에만 남은 초를 주고 키에 TTL이 있는지 확인한다."""
    name = f"t-{uniq('fail').split('@')[0]}"
    key = "ip:10.0.0.8"
    rkey = f"rl:{name}:{key}"
    lim = RedisFixedWindowLimiter(max_per_min=2, name=name, client=redis_client)
    try:
        assert await lim.blocked(key) is None
        assert await lim.add(key) is None
        assert await lim.add(key) is None
        wait = await lim.blocked(key)
        assert wait is not None and 0 < wait <= 60
        assert await lim.add(key) is not None
        assert 0 < redis_client.ttl(rkey) <= 60
    finally:
        redis_client.delete(rkey)
