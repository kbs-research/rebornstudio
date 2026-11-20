"""MAIU reconciler의 슬롯 카운터.

정상 사이클과 보류(deferred) 재처리 사이클을 10:1로 배분한다.
정상 사이클만 계속 돌면 실패해 보류된 행이 재처리되지 않는다.
그래서 정상 사이클 10회마다 보류 사이클 1회를 보장한다.

Redis Lua 스크립트로 INCR와 비교를 원자적으로 실행한다.
그래서 워커 여럿이 동시에 호출해도 11번 호출마다 정확히 1번만 보류 슬롯을 받는다.

Redis에 연결하지 못하면 인메모리 카운터를 쓴다.
인메모리 카운터는 단일 프로세스(dev/test 환경)를 전제로 한다.
"""
from __future__ import annotations

import logging
from threading import Lock

from api_gateway.config import settings

log = logging.getLogger(__name__)

# 정상 사이클과 보류 사이클의 비율은 (`_RATIO` - 1) : 1이다.
# 값이 11이면 11번째 호출마다 보류 슬롯을 준다.
_RATIO = 11
_KEY = "maiu:slot:cycle"

# INCR한 값이 비율에 닿으면 카운터를 0으로 되돌리고 'deferred'를 돌려주는 Lua 스크립트.
# 그 밖의 경우에는 'normal'을 돌려준다.
_LUA = """
local key = KEYS[1]
local ratio = tonumber(ARGV[1])
local current = tonumber(redis.call('INCR', key))
if current >= ratio then
  redis.call('SET', key, 0)
  return 'deferred'
end
return 'normal'
"""


class _LRUSlotCounter:
    """Redis에 연결하지 못할 때 쓰는 인메모리 카운터. 단일 프로세스 안에서만 10:1을 지킨다."""

    def __init__(self) -> None:
        """카운터를 0으로 두고 잠금 객체를 만든다."""
        self._count = 0
        self._lock = Lock()

    def acquire(self) -> str:
        """이번 사이클의 슬롯 종류를 돌려준다. 'normal' 또는 'deferred'다."""
        with self._lock:
            self._count += 1
            if self._count >= _RATIO:
                self._count = 0
                return "deferred"
            return "normal"

    def reset(self) -> None:
        """카운터를 0으로 되돌린다. 테스트에서 쓴다."""
        with self._lock:
            self._count = 0


class _RedisSlotCounter:
    """Redis Lua 스크립트를 쓰는 카운터. 워커가 여럿이어도 원자적으로 10:1을 지킨다."""

    def __init__(self, client) -> None:
        """Redis 클라이언트를 받고 Lua 스크립트를 등록한다."""
        self.r = client
        self._acquire = client.register_script(_LUA)

    def acquire(self) -> str:
        """Lua 스크립트를 실행해 슬롯 종류를 돌려준다. bytes 응답은 str로 바꾼다."""
        result = self._acquire(keys=[_KEY], args=[_RATIO])
        if isinstance(result, bytes):
            return result.decode("utf-8")
        return result

    def reset(self) -> None:
        """Redis 키를 지운다. 테스트에서 쓴다."""
        self.r.delete(_KEY)


_counter: _LRUSlotCounter | _RedisSlotCounter | None = None


def get_counter() -> _LRUSlotCounter | _RedisSlotCounter:
    """슬롯 카운터를 돌려준다. 처음 부를 때 만들고 이후에는 같은 카운터를 돌려준다.

    `settings.redis_url`에 연결되면 Redis 카운터를 만들고, 연결에 실패하면 인메모리 카운터를 만든다.
    주의: 이 함수는 `redis_infra`를 거치지 않는다. 그래서 환경과 관계없이 연결에 실패하면
    경고 로그만 남기고 인메모리 카운터로 대체한다.
    """
    global _counter
    if _counter is not None:
        return _counter
    try:
        import redis
        client = redis.from_url(settings.redis_url, socket_timeout=2)
        client.ping()
        _counter = _RedisSlotCounter(client)
        from api_gateway import redis_infra
        log.info("maiu_slot_counter: RedisSlotCounter (%s)", redis_infra.redis_location())
    except Exception as e:  # noqa: BLE001
        log.warning("maiu_slot_counter: Redis 실패 (%s) → LRU fallback", e)
        _counter = _LRUSlotCounter()
    return _counter


def set_counter(c: _LRUSlotCounter | _RedisSlotCounter | None) -> None:
    """카운터를 주입하거나 None으로 초기화한다. 테스트에서 쓴다."""
    global _counter
    _counter = c
