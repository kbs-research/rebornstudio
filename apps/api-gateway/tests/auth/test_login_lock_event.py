"""이메일 단위 로그인 잠금이 걸리면 감사 기록(`auth.login_locked`)을 한 번 남기는지 보는 테스트.

이메일 단위 잠금(50회 실패, 15분)은 맞는 비밀번호도 막는다. 여러 IP에서 남의 이메일로 틀리기만
해도 그 사람의 로그인을 막을 수 있으므로, 잠금이 걸린 사실이 본인 로그인 이력과 관리자의 사용자
활동 이력에 남아야 한다. 잠금 중의 시도는 429로 끝나 실패를 다시 세지 않으므로 기록도 늘지 않는다.
"""
import json
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent
from api_gateway.services.auth import login_throttle, rate_limit
from httpx import AsyncClient
from sqlalchemy import func, select
from tests._helpers import make_account
from tests.conftest import uniq

_WRONG = "WrongLock123!@abcd"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Chrome/120.0 lock-test"


@pytest.fixture(autouse=True)
def _reset_state():
    """로그인 실패 잠금과 요청 한도를 테스트마다 비운다."""
    login_throttle.reset()
    rate_limit.reset_for_test()
    yield
    login_throttle.reset()
    rate_limit.reset_for_test()


def _fail_from_many_ips(email: str, times: int) -> None:
    """여러 IP에서 같은 이메일로 실패를 기록한다. 쌍 단위 5회 잠금에는 걸리지 않게 IP를 나눈다."""
    for i in range(times):
        login_throttle.record_failure(email, f"10.77.{i // 4}.{i % 4}")


async def _lock_events(uid: str) -> list[DomainEvent]:
    """그 사용자를 대상으로 남은 로그인 잠금 감사 행을 읽는다."""
    async with SessionLocal() as s:
        return list((await s.scalars(
            select(DomainEvent).where(
                DomainEvent.target_id == UUID(uid),
                DomainEvent.event_type == "auth.login_locked",
            )
        )).all())


async def _all_lock_rows() -> int:
    """전체 로그인 잠금 감사 행 수를 센다."""
    async with SessionLocal() as s:
        return int(await s.scalar(
            select(func.count()).select_from(DomainEvent)
            .where(DomainEvent.event_type == "auth.login_locked")
        ) or 0)


async def test_crossing_threshold_writes_one_event(client: AsyncClient, admin_token: str):
    """50번째 실패로 이메일이 잠기면 감사 행이 하나 남고 접속 출처와 잠금 정보를 싣는지 확인한다."""
    uid, email, _pwd, _tok = await make_account(client, admin_token, "lockev")
    _fail_from_many_ips(email, 49)
    assert await _lock_events(uid) == []

    r = await client.post(
        "/auth/login", json={"email": email, "password": _WRONG}, headers={"User-Agent": _UA},
    )
    assert r.status_code == 401, r.text
    events = await _lock_events(uid)
    assert len(events) == 1
    ev = events[0]
    assert ev.target_kind == "user" and ev.kind == "audit"
    assert ev.actor_user_id == UUID(uid)
    assert ev.payload["via"] == "login"
    assert ev.payload["failures"] == 50 and ev.payload["lock_seconds"] == 900
    assert ev.payload["user_agent"] == _UA
    assert ev.payload["ip"]


async def test_attempts_during_lock_add_no_event(client: AsyncClient, admin_token: str):
    """잠금 중의 시도는 맞는 비밀번호든 틀린 비밀번호든 429이고 감사 행이 늘지 않는지 확인한다."""
    uid, email, pwd, _tok = await make_account(client, admin_token, "lockhold")
    _fail_from_many_ips(email, 49)
    r = await client.post("/auth/login", json={"email": email, "password": _WRONG})
    assert r.status_code == 401, r.text
    assert len(await _lock_events(uid)) == 1

    for pw in (pwd, _WRONG, _WRONG):
        r = await client.post("/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 429, r.text
    assert len(await _lock_events(uid)) == 1


async def test_next_lock_window_writes_again(client: AsyncClient, admin_token: str):
    """잠금이 풀린 뒤 다시 50회 실패해 새로 잠기면 감사 행이 하나 더 남는지 확인한다."""
    uid, email, _pwd, _tok = await make_account(client, admin_token, "lockagain")
    _fail_from_many_ips(email, 49)
    await client.post("/auth/login", json={"email": email, "password": _WRONG})
    login_throttle.clear_all(email)  # 잠금 만료와 같은 상태로 되돌린다
    _fail_from_many_ips(email, 49)
    r = await client.post("/auth/login", json={"email": email, "password": _WRONG})
    assert r.status_code == 401, r.text
    assert len(await _lock_events(uid)) == 2


async def test_unknown_email_lock_writes_nothing(client: AsyncClient):
    """없는 이메일이 잠겨도 감사 행을 남기지 않고 500 없이 401과 429로 답하는지 확인한다."""
    ghost = uniq("lockghost")
    before = await _all_lock_rows()
    _fail_from_many_ips(ghost, 49)
    r = await client.post("/auth/login", json={"email": ghost, "password": _WRONG})
    assert r.status_code == 401, r.text
    r = await client.post("/auth/login", json={"email": ghost, "password": _WRONG})
    assert r.status_code == 429, r.text
    assert await _all_lock_rows() == before


async def test_payload_has_no_secrets(client: AsyncClient, admin_token: str):
    """감사 행에 비밀번호나 이메일 같은 입력값이 실리지 않고 정해진 키만 있는지 확인한다."""
    uid, email, _pwd, _tok = await make_account(client, admin_token, "locksecret")
    _fail_from_many_ips(email, 49)
    await client.post(
        "/auth/login", json={"email": email, "password": _WRONG}, headers={"User-Agent": _UA},
    )
    (ev,) = await _lock_events(uid)
    assert set(ev.payload) == {"ip", "user_agent", "via", "failures", "lock_seconds"}
    raw = json.dumps(ev.payload)
    assert _WRONG not in raw and "password" not in raw and email not in raw


async def test_password_change_lock_writes_event(client: AsyncClient, admin_token: str):
    """비밀번호 변경의 현재 비밀번호 실패로 이메일이 잠겨도 같은 감사 행이 남는지 확인한다."""
    uid, email, _pwd, tok = await make_account(client, admin_token, "lockchg")
    _fail_from_many_ips(email, 49)
    r = await client.post(
        "/auth/password/change", headers={"Authorization": f"Bearer {tok}", "User-Agent": _UA},
        json={"old_password": _WRONG, "new_password": "BrandNew123!@#xy"},
    )
    assert r.status_code == 401, r.text
    events = await _lock_events(uid)
    assert len(events) == 1
    assert events[0].payload["via"] == "password_change"
    assert events[0].payload["user_agent"] == _UA


async def test_lock_shows_in_own_history_and_admin_activity(client: AsyncClient, admin_token: str):
    """잠금 기록이 본인 로그인 이력과 관리자의 사용자 활동 이력에 나오는지 확인한다."""
    uid, email, _pwd, tok = await make_account(client, admin_token, "lockshow")
    _fail_from_many_ips(email, 49)
    await client.post("/auth/login", json={"email": email, "password": _WRONG})

    own = (await client.get("/me/auth-events", headers={"Authorization": f"Bearer {tok}"})).json()
    assert [e for e in own if e["event_type"] == "auth.login_locked"], own
    activity = (await client.get(
        f"/admin/users/{uid}/activity", headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert [e for e in activity if e["event_type"] == "auth.login_locked"], activity


def test_record_failure_reports_only_the_email_lock_start():
    """`record_failure`가 이메일 단위 잠금이 시작된 호출에서만 참을 돌려주는지 확인한다.

    쌍 단위 5회 잠금은 그 IP만 막으므로 잠금 시작으로 세지 않는다.
    """
    email = uniq("lockret")
    assert [login_throttle.record_failure(email, "198.51.100.40") for _ in range(5)] == [False] * 5
    results = [login_throttle.record_failure(email, f"10.78.{i // 4}.{i % 4}") for i in range(45)]
    assert results[-1] is True and not any(results[:-1])


def test_memory_backend_reports_lock_start_once():
    """인메모리 백엔드도 임계에 닿은 호출에서만 참이고 잠금 중의 호출은 거짓인지 확인한다."""
    backend = login_throttle.MemoryBackend()
    assert [backend.record_failure("k", 3) for _ in range(4)] == [False, False, True, False]


def test_redis_backend_reports_lock_start_once():
    """Redis 백엔드도 잠금 키를 새로 만든 호출에서만 참인지 확인한다.

    레플리카 여럿이 같은 잠금을 거의 동시에 걸어도 감사 행이 한 번만 남는다.
    """
    import redis
    from api_gateway.config import settings
    try:
        client = redis.from_url(settings.redis_url, socket_timeout=2, decode_responses=True)
        client.ping()
    except Exception:
        pytest.skip(f"Redis 없음({settings.redis_url})")
    backend = login_throttle.RedisBackend(client)
    key = uniq("lockredis")
    try:
        assert [backend.record_failure(key, 3) for _ in range(4)] == [False, False, True, False]
    finally:
        backend.clear(key)
