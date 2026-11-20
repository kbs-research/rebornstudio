"""로그인 실패 제한. Redis 공유 카운터와 잠금을 쓴다.

로드밸런서가 요청을 레플리카에 나눠 보내므로, 레플리카가 N개면 인메모리 카운터는
잠금 임계를 사실상 5×N회로 늘린다.
보안 기능이 드러나지 않게 무력화되므로 공유 저장소가 필요하다.
백엔드 선택은 `redis_infra` 규칙을 따른다.
local/test가 아닌 환경은 Redis가 필수이고 없으면 기동이 실패한다.
local/test만 인메모리 백엔드를 쓴다.

카운터는 두 단위다.
- (이메일, 클라이언트 IP) 쌍은 연속 5회 실패하면 15분 동안 잠근다.
- 이메일 하나는 IP와 관계없이 50회 실패하면 15분 동안 잠근다.
이메일 하나만으로 5회에 잠그면 남의 이메일로 5번 틀리는 것만으로 그 사람의 로그인을
막을 수 있다. 쌍 단위만 두면 IP를 바꿔 가며 대입하는 시도를 막지 못하므로 이메일 단위의
임계를 크게 따로 둔다. IP는 `audit.client_ip`의 판정(신뢰 프록시 설정)을 그대로 쓴다.

동작 규칙은 두 백엔드가 같다.
- 잠금이 끝난 뒤의 실패는 카운터를 새로 센다.
- 로그인에 성공하면 그 쌍과 이메일 단위 카운터를 지운다.
- `record_failure`는 이메일 단위 잠금이 그 호출로 시작됐는지 돌려준다. 부르는 쪽이 감사 기록을
  남긴다. 잠금 키를 새로 만든 호출만 참이라 레플리카가 여럿이어도 잠금 하나에 기록이 하나다.
잠금 중에는 `retry_after`가 먼저 429를 만들므로 `record_failure`는 잠금 중에 불리지 않는다
(`auth/service.py` 로그인 경로의 호출 순서).
키는 모듈 공개 함수가 만든다(`_email_key`·`_pair_key`). 백엔드는 받은 키를 그대로 쓴다.
"""
from __future__ import annotations

import threading
import time
from typing import Protocol

from api_gateway import redis_infra

_MAX_FAILS = 5          # (이메일, IP) 쌍이 잠기는 연속 실패 횟수
_MAX_FAILS_EMAIL = 50   # 이메일 하나가 IP와 관계없이 잠기는 실패 횟수
_LOCK_SECONDS = 900     # 잠금 지속 시간(초). 15분이다.
_RETENTION = 3600       # 실패 카운터 보존 시간(초). 1시간 동안 실패가 없으면 사라진다.

# 실패 카운터와 잠금의 Redis 키 형식. `{email}` 자리에는 이메일 키나 쌍 키가 들어간다.
_FAIL_KEY = "throttle:login:fail:{email}"
_LOCK_KEY = "throttle:login:lock:{email}"
# 쌍 키의 접두사. 이메일 형식 검사를 거친 주소는 `ip:`로 시작하지 않아 이메일 키와 겹치지 않는다.
_PAIR_PREFIX = "ip:"


class ThrottleBackend(Protocol):
    """이메일 단위 실패 카운터와 잠금 백엔드의 인터페이스."""

    def retry_after(self, email: str) -> int | None:
        """잠겨 있으면 남은 초(>0)를, 아니면 None을 돌려준다."""

    def record_failure(self, email: str, max_fails: int = _MAX_FAILS) -> bool:
        """실패를 1회 기록하고 `max_fails`에 닿으면 잠근다. 이 호출로 잠금이 시작됐으면 True다."""

    def clear(self, email: str) -> None:
        """로그인 성공 시 카운터와 잠금을 지운다."""

    def clear_email(self, email: str) -> None:
        """이메일 키와 그 이메일의 모든 쌍 키를 지운다."""

    def reset_for_test(self) -> None:
        """전체 상태를 초기화한다. 테스트 격리 전용이다."""


class MemoryBackend:
    """단일 프로세스용 인메모리 백엔드. local/test에서만 쓴다(redis_infra 규칙이 보장한다)."""

    def __init__(self) -> None:
        """빈 상태와 잠금 객체를 만든다."""
        self._lock = threading.Lock()
        # email → (fail_count, first_fail_ts, locked_until)
        self._state: dict[str, tuple[int, float, float]] = {}
        self._clock = time.time  # 테스트가 시계를 바꿔 끼울 수 있게 속성으로 둔다

    def retry_after(self, email: str) -> int | None:
        """잠겨 있으면 남은 초(>0)를, 아니면 None을 돌려준다."""
        with self._lock:
            entry = self._state.get(email)
            if not entry:
                return None
            remaining = int(entry[2] - self._clock())
            return remaining if remaining > 0 else None

    def record_failure(self, email: str, max_fails: int = _MAX_FAILS) -> bool:
        """실패를 1회 기록하고 `max_fails`에 닿으면 잠근다. 오래된 항목도 함께 정리한다.

        이미 잠긴 키의 실패는 잠금 시작이 아니므로 False다.
        """
        with self._lock:
            now = self._clock()
            count, first_ts, locked_until = self._state.get(email, (0, now, 0.0))
            if locked_until and now >= locked_until:  # 잠금이 끝났으면 처음부터 다시 센다
                count, first_ts, locked_until = 0, now, 0.0
            was_locked = locked_until > now
            count += 1
            if count >= max_fails:
                locked_until = now + _LOCK_SECONDS
            self._state[email] = (count, first_ts, locked_until)
            # 잠겨 있지 않고 보존 기간이 지난 항목을 정리한다.
            stale = [
                e for e, (_, ts, lu) in self._state.items()
                if now >= lu and now - ts > _RETENTION
            ]
            for e in stale:
                self._state.pop(e, None)
            return not was_locked and locked_until > now

    def clear(self, email: str) -> None:
        """이 키의 카운터와 잠금을 지운다."""
        with self._lock:
            self._state.pop(email, None)

    def clear_email(self, email: str) -> None:
        """이메일 키와 그 이메일로 끝나는 모든 쌍 키를 지운다."""
        suffix = f"|{email}"
        with self._lock:
            for k in [k for k in self._state
                      if k == email or (k.startswith(_PAIR_PREFIX) and k.endswith(suffix))]:
                self._state.pop(k, None)

    def reset_for_test(self) -> None:
        """전체 상태를 비운다. 테스트 격리 전용이다."""
        with self._lock:
            self._state.clear()


class RedisBackend:
    """레플리카가 공유하는 Redis 백엔드.

    카운터는 INCR+EXPIRE, 잠금은 SET EX로 둔다. 보존과 만료는 TTL이 처리한다.
    INCR와 EXPIRE는 한 트랜잭션으로 보낸다. 따로 보내면 사이에 연결이 끊긴 카운터가 TTL 없이
    남아 실패 기록이 지워지지 않는다.
    """

    def __init__(self, client) -> None:
        """공유 Redis 클라이언트를 받는다."""
        self.r = client

    def retry_after(self, email: str) -> int | None:
        """잠금 키의 남은 TTL(초)을 돌려준다. 잠겨 있지 않으면 None이다."""
        ttl = self.r.ttl(_LOCK_KEY.format(email=email))
        return ttl if ttl and ttl > 0 else None

    def record_failure(self, email: str, max_fails: int = _MAX_FAILS) -> bool:
        """실패 카운터를 올리고 `max_fails`에 닿으면 잠근다. 잠금 키를 새로 만들었으면 True다.

        잠금은 `SET NX`로 건다. 두 레플리카가 거의 동시에 임계에 닿아도 한쪽만 잠금을 만들어
        잠금 시작 기록이 한 번만 남는다. 이미 있는 잠금의 남은 시간은 늘리지 않는다.
        """
        fail_key = _FAIL_KEY.format(email=email)
        pipe = self.r.pipeline(transaction=True)
        pipe.incr(fail_key)
        pipe.expire(fail_key, _RETENTION, nx=True)
        n, _ = pipe.execute()
        if n < max_fails:
            return False
        # 잠그고 카운터를 지운다. 잠금이 끝난 뒤의 실패는 1부터 센다(메모리 구현과 같은 규칙).
        started = self.r.set(_LOCK_KEY.format(email=email), "1", ex=_LOCK_SECONDS, nx=True)
        self.r.delete(fail_key)
        return bool(started)

    def clear(self, email: str) -> None:
        """이 키의 카운터 키와 잠금 키를 지운다."""
        self.r.delete(_FAIL_KEY.format(email=email), _LOCK_KEY.format(email=email))

    def clear_email(self, email: str) -> None:
        """이메일 키와 그 이메일의 모든 쌍 키를 지운다. 쌍 키는 SCAN으로 찾는다.

        이메일에 glob 특수 문자가 있어도 다른 이메일의 키를 지우지 않도록 이스케이프한다.
        재설정 확정처럼 드문 경로만 부른다.
        """
        self.clear(email)
        esc = "".join(f"\\{c}" if c in "*?[]\\" else c for c in email)
        for tmpl in (_FAIL_KEY, _LOCK_KEY):
            pattern = tmpl.format(email=f"{_PAIR_PREFIX}*|{esc}")
            for key in self.r.scan_iter(match=pattern, count=500):
                self.r.delete(key)

    def reset_for_test(self) -> None:
        """로그인 제한 키를 모두 지운다. 테스트 격리 전용이다."""
        for pattern in ("throttle:login:fail:*", "throttle:login:lock:*"):
            for key in self.r.scan_iter(match=pattern, count=500):
                self.r.delete(key)


#: 처음 쓸 때 고른 백엔드.
_backend: ThrottleBackend | None = None


def _get_backend() -> ThrottleBackend:
    """`redis_infra` 규칙대로 백엔드를 고른다. Redis가 없으면(local/test) 메모리 백엔드다."""
    global _backend
    if _backend is None:
        client = redis_infra.get_redis()
        _backend = RedisBackend(client) if client is not None else MemoryBackend()
    return _backend


def set_backend(backend: ThrottleBackend | None) -> None:
    """백엔드를 직접 주입하거나 None으로 두어 다음 호출에서 다시 고르게 한다. 테스트 전용이다."""
    global _backend
    _backend = backend


# --- 모듈 공개 API. auth/service.py가 이 시그니처로 부른다 -------------------

def _email_key(email: str) -> str:
    """잠금 키로 쓸 이메일을 앞뒤 공백을 떼고 소문자로 맞춘다.

    `users.email`은 CITEXT라 대소문자만 다른 이메일도 같은 계정을 찾는다.
    받은 문자열을 그대로 키로 쓰면 변형마다 카운터가 따로 생겨 계정당 잠금 임계가 무력해진다.
    """
    return email.strip().lower()


def _pair_key(email: str, ip: str | None) -> str:
    """(이메일, IP) 쌍의 키를 만든다. 주소를 모르면 `unknown` 하나로 센다."""
    return f"{_PAIR_PREFIX}{ip or 'unknown'}|{_email_key(email)}"


def retry_after(email: str, ip: str | None = None) -> int | None:
    """쌍 잠금과 이메일 단위 잠금 중 더 긴 남은 초를 돌려준다. 둘 다 없으면 None이다."""
    backend = _get_backend()
    waits = [
        w for w in (backend.retry_after(_pair_key(email, ip)),
                    backend.retry_after(_email_key(email)))
        if w is not None
    ]
    return max(waits) if waits else None


def record_failure(email: str, ip: str | None = None) -> bool:
    """로그인 실패를 쌍과 이메일 단위에 1회씩 기록한다. 각 임계에 닿으면 잠근다.

    Returns:
        이메일 단위 잠금이 이 호출로 시작됐으면 True. 쌍 단위 잠금은 그 IP만 막으므로 세지 않는다.
    """
    backend = _get_backend()
    backend.record_failure(_pair_key(email, ip), _MAX_FAILS)
    return backend.record_failure(_email_key(email), _MAX_FAILS_EMAIL)


#: 이메일 단위 잠금의 임계. 잠금 감사 기록이 같은 값을 싣는다.
MAX_FAILS_EMAIL = _MAX_FAILS_EMAIL
#: 잠금 지속 시간(초). 잠금 감사 기록이 같은 값을 싣는다.
LOCK_SECONDS = _LOCK_SECONDS


def clear(email: str, ip: str | None = None) -> None:
    """로그인 성공 시 그 쌍과 이메일 단위의 카운터와 잠금을 지운다."""
    backend = _get_backend()
    backend.clear(_pair_key(email, ip))
    backend.clear(_email_key(email))


def clear_all(email: str) -> None:
    """이메일 단위와 모든 IP의 쌍 카운터와 잠금을 지운다. 비밀번호 재설정 확정이 부른다."""
    _get_backend().clear_email(_email_key(email))


def reset() -> None:
    """백엔드와 관계없이 전체 상태를 초기화한다. 테스트 전용이다."""
    _get_backend().reset_for_test()
