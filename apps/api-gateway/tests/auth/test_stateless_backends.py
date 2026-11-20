"""레플리카가 N개여도 보안 동작이 N=1일 때와 같은지 보는 테스트.

잠금, 요청 한도, 토큰 폐기 상태는 프로세스 밖(Redis)에 있다.
그래서 서로 다른 백엔드 인스턴스(서로 다른 레플리카)가 같은 상태를 본다.
각 테스트는 독립 인스턴스 둘을 만들고, A에 기록한 것이 B에 보이는지 검사한다.
compose --scale 검증을 자동화한 근사 검사다.

Redis가 없으면 skip한다(로컬 오프라인 개발). CI에는 Redis 서비스가 있어 항상 돈다.
"""
import pytest
from api_gateway.config import settings
from api_gateway.services.auth import jwt_revocation, login_throttle
from api_gateway.services.auth.rate_limit import RedisFixedWindowLimiter
from fastapi import HTTPException
from tests.conftest import uniq


@pytest.fixture(scope="module")
def redis_client():
    """실제 Redis에 연결한다. 연결할 수 없으면 이 파일 전체를 skip한다."""
    import redis
    try:
        c = redis.from_url(settings.redis_url, socket_timeout=2, decode_responses=True)
        c.ping()
    except Exception:
        pytest.skip(f"Redis 없음({settings.redis_url}) — 레플리카 공유 검증 skip")
    return c


def _fresh(redis_client):
    """새 연결로 클라이언트를 만든다. 연결·프로세스 상태를 공유하지 않는 다른 레플리카를 흉내 낸다."""
    import redis
    return redis.from_url(settings.redis_url, socket_timeout=2, decode_responses=True)


def test_login_lock_is_shared_across_instances(redis_client):
    """레플리카 A에서 5회 실패하면 레플리카 B가 잠금을 보고, B에서 해제하면 A도 풀리는지 확인한다.

    잠금이 인메모리라면 B의 카운터는 0이라서 임계가 사실상 5×N회로 늘어난다.
    """
    a = login_throttle.RedisBackend(redis_client)
    b = login_throttle.RedisBackend(_fresh(redis_client))
    email = uniq("replica")
    try:
        for _ in range(5):
            a.record_failure(email)
        remaining = b.retry_after(email)
        assert remaining is not None and remaining > 0, "B 레플리카가 잠금을 못 본다"
        b.clear(email)
        assert a.retry_after(email) is None, "B의 해제를 A가 못 본다"
    finally:
        a.clear(email)


def test_lock_resets_count_after_expiry_semantics(redis_client):
    """잠금을 걸 때 실패 카운터를 지우는지 확인한다. 그래야 잠금 만료 뒤 실패를 메모리 구현처럼 1부터 다시 센다."""
    backend = login_throttle.RedisBackend(redis_client)
    email = uniq("resetsem")
    try:
        for _ in range(5):
            backend.record_failure(email)
        assert redis_client.exists(f"throttle:login:fail:{email}") == 0, (
            "잠금 후 카운터가 남아 있다 — 만료 후 첫 실패가 곧바로 재잠금된다"
        )
    finally:
        backend.clear(email)


async def test_rate_limit_counts_shared_across_instances(redis_client):
    """한도가 5일 때 A가 3회를 쓰면 B에는 2회만 남고 6번째 요청이 429인지 확인한다."""
    name = f"t-{uniq('rl').split('@')[0]}"
    a = RedisFixedWindowLimiter(max_per_min=5, name=name, client=redis_client)
    b = RedisFixedWindowLimiter(max_per_min=5, name=name, client=_fresh(redis_client))
    key = "10.0.0.1"
    try:
        for _ in range(3):
            await a.hit(key)
        for _ in range(2):
            await b.hit(key)
        with pytest.raises(HTTPException) as e:
            await b.hit(key)
        assert e.value.status_code == 429
    finally:
        redis_client.delete(f"rl:{name}:{key}")


def test_non_local_without_redis_fails_fast(monkeypatch):
    """non-local 환경에서 Redis에 연결할 수 없으면 기동이 실패하는지 확인한다.

    인메모리로 강등하지 않는다.
    강등을 허용하면 재시작이 몰릴 때 Redis 연결이 불안정한 레플리카만 토큰 폐기·잠금 상태를 보지 못한다.
    그러면 레플리카마다 보안 판정이 달라진다.
    연결 실패가 전제라서 이 테스트는 Redis가 없어도 돈다.
    """
    from api_gateway import redis_infra
    redis_infra.reset_for_test()
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")  # 연결 불가 주소
    try:
        with pytest.raises(RuntimeError, match="인메모리 강등은 금지"):
            redis_infra.get_redis()
    finally:
        redis_infra.reset_for_test()  # 판정 캐시를 남기면 이후 테스트가 오염된다


def test_revocation_visible_across_instances(redis_client):
    """A 레플리카에서 폐기한 jti·family를 B 레플리카가 바로 보는지 확인한다."""
    a = jwt_revocation.RedisBackend(redis_client)
    b = jwt_revocation.RedisBackend(_fresh(redis_client))
    jti, fam = uniq("jti"), uniq("fam")
    try:
        a.revoke_jti(jti, ttl_seconds=60)
        a.revoke_family(fam, ttl_seconds=60)
        assert b.is_jti_revoked(jti), "로그아웃 토큰이 B 레플리카에서 살아 있다"
        assert b.is_family_revoked(fam)
    finally:
        redis_client.delete(f"revoked:jti:{jti}", f"revoked:fam:{fam}")


def test_redis_location_drops_credentials():
    """기동 로그에 쓰는 Redis 주소가 사용자 이름·비밀번호를 빼고 호스트·포트·DB 번호만 남기는지 확인한다."""
    from api_gateway.redis_infra import redis_location

    assert redis_location("redis://user:s3cret@redis:6400/3") == "redis:6400/3"
    assert redis_location("redis://:s3cret@redis:6379/0") == "redis:6379/0"
    assert redis_location("redis://localhost") == "localhost:6379/0"
