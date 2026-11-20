"""JWT 무효화(revocation) 백엔드. 운영은 Redis를, local/test는 인메모리 LRU를 쓴다.

설계:
- 토큰 payload에 `jti`(고유 id)와 `fam`(refresh family id)을 넣는다.
- 토큰을 무효화하면 `revoked:jti:{jti}`를 SET NX한다.
  값은 폐기 시각(epoch 초), TTL은 토큰 만료까지다.
  여러 프로세스가 같은 기록을 본다. NX라서 같은 jti를 두 요청이 동시에 폐기해도 한쪽만
  "처음 폐기"로 판정된다. refresh 교체는 이 판정으로 같은 토큰의 동시 교체를 하나만 통과시킨다.
- family 무효화(탈취 탐지, 전체 로그아웃)는 `revoked:fam:{fam}`를 SET한다.
  같은 family의 access·refresh 토큰이 모두 즉시 무효가 된다.

백엔드 선택은 `redis_infra` 규칙을 따른다.
- Redis 클라이언트가 있으면 `RedisBackend`를 쓴다.
- 없으면 `LRUBackend`(최대 10000건)를 쓴다. 이 경우는 local/test에서만 생긴다.
  다른 환경에서 Redis가 없으면 `redis_infra`가 기동을 실패시킨다.

테스트 격리: `reset_for_test()`가 무효화 기록을 모두 지운다(LRU와 Redis 모두).
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Protocol

log = logging.getLogger(__name__)
#: 인메모리 백엔드가 보관하는 최대 건수.
_LRU_MAX = 10000
_TTL_MIN = 60  # 최소 TTL(초). 만료가 지난 토큰에 음수나 0이 들어가지 않게 한다.


class RevocationBackend(Protocol):
    """jti·family 단위 무효화 백엔드의 인터페이스."""

    def revoke_jti(self, jti: str, ttl_seconds: int) -> bool:
        """jti를 무효화 목록에 넣고 처음 넣었으면 True를 돌려준다. TTL이 지나면 자동으로 빠진다."""

    def jti_revoked_at(self, jti: str) -> float | None:
        """jti를 폐기한 시각(epoch 초)을 돌려준다. 폐기되지 않았으면 None이다."""

    def revoke_family(self, fam: str, ttl_seconds: int) -> None:
        """family 전체를 무효화 목록에 넣는다."""

    def is_jti_revoked(self, jti: str) -> bool:
        """jti가 무효화됐으면 True를 돌려준다."""

    def is_family_revoked(self, fam: str) -> bool:
        """family가 무효화됐으면 True를 돌려준다."""

    def reset_for_test(self) -> None:
        """무효화 기록을 모두 지운다. 테스트 격리 전용이다."""


class LRUBackend:
    """OrderedDict 기반 인메모리 백엔드. 단일 프로세스인 local/test 환경에서 쓴다.

    TTL은 적용하지 않는다. 최대 건수(`max_size`)를 넘으면 오래된 항목부터 뺀다.
    """

    def __init__(self, max_size: int = _LRU_MAX) -> None:
        """jti·family 저장소를 비운 상태로 만든다."""
        self._jti: OrderedDict[str, float] = OrderedDict()  # jti → 폐기 시각
        self._fam: OrderedDict[str, float] = OrderedDict()
        self.max_size = max_size

    def _add(self, store: OrderedDict[str, float], key: str) -> None:
        """키를 지금 시각과 함께 가장 최근 위치에 넣고, 최대 건수를 넘으면 가장 오래된 키를 뺀다."""
        store[key] = time.time()
        store.move_to_end(key)
        while len(store) > self.max_size:
            store.popitem(last=False)

    def revoke_jti(self, jti: str, ttl_seconds: int) -> bool:
        """jti를 무효화 목록에 넣고 처음 넣었으면 True를 돌려준다. `ttl_seconds`는 쓰지 않는다.

        이미 있으면 폐기 시각을 바꾸지 않는다. 확인과 기록 사이에 await가 없어 한 이벤트 루프
        안에서는 원자적이다.
        """
        if jti in self._jti:
            return False
        self._add(self._jti, jti)
        return True

    def jti_revoked_at(self, jti: str) -> float | None:
        """jti를 폐기한 시각을 돌려준다. 폐기되지 않았으면 None이다."""
        return self._jti.get(jti)

    def revoke_family(self, fam: str, ttl_seconds: int) -> None:
        """family를 무효화 목록에 넣는다. `ttl_seconds`는 쓰지 않는다."""
        self._add(self._fam, fam)

    def is_jti_revoked(self, jti: str) -> bool:
        """jti가 무효화 목록에 있으면 True를 돌려준다."""
        return jti in self._jti

    def is_family_revoked(self, fam: str) -> bool:
        """family가 무효화 목록에 있으면 True를 돌려준다."""
        return fam in self._fam

    def reset_for_test(self) -> None:
        """두 저장소를 비운다. 테스트 격리 전용이다."""
        self._jti.clear()
        self._fam.clear()


class RedisBackend:
    """Redis SET과 TTL을 쓰는 백엔드. 여러 프로세스가 공유하고 만료는 Redis가 처리한다."""

    def __init__(self, client) -> None:
        """공유 Redis 클라이언트를 받는다."""
        self.r = client

    def revoke_jti(self, jti: str, ttl_seconds: int) -> bool:
        """`revoked:jti:{jti}`를 SET NX하고 처음 기록했으면 True를 돌려준다.

        값은 폐기 시각(epoch 초)이고 TTL은 최소 `_TTL_MIN`초다.
        NX라서 레플리카가 여럿이어도 같은 jti의 처음 폐기는 한 요청만 얻는다.
        """
        ok = self.r.set(
            f"revoked:jti:{jti}", f"{time.time():.3f}", ex=max(_TTL_MIN, ttl_seconds), nx=True,
        )
        return bool(ok)

    def jti_revoked_at(self, jti: str) -> float | None:
        """`revoked:jti:{jti}`의 값(폐기 시각)을 돌려준다. 키가 없으면 None이다.

        값이 "1"인 키(폐기 시각을 싣지 않은 키)는 아주 오래전 폐기로 읽는다.
        """
        v = self.r.get(f"revoked:jti:{jti}")
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    def revoke_family(self, fam: str, ttl_seconds: int) -> None:
        """`revoked:fam:{fam}`를 SET한다. TTL은 최소 `_TTL_MIN`초다."""
        self.r.set(f"revoked:fam:{fam}", "1", ex=max(_TTL_MIN, ttl_seconds))

    def is_jti_revoked(self, jti: str) -> bool:
        """`revoked:jti:{jti}` 키가 있으면 True를 돌려준다."""
        return self.r.exists(f"revoked:jti:{jti}") > 0

    def is_family_revoked(self, fam: str) -> bool:
        """`revoked:fam:{fam}` 키가 있으면 True를 돌려준다."""
        return self.r.exists(f"revoked:fam:{fam}") > 0

    def reset_for_test(self) -> None:
        """무효화 키를 모두 지운다. 테스트 격리 전용이다."""
        # `revoked:jti:*`·`revoked:fam:*` 키를 모두 지운다. 테스트 접두사로 좁히지 않는다.
        # 운영 Redis에서 부르지 않게 막는 것은 호출자 책임이다.
        for pattern in ("revoked:jti:*", "revoked:fam:*"):
            for key in self.r.scan_iter(match=pattern, count=500):
                self.r.delete(key)


#: 처음 쓸 때 고른 무효화 백엔드.
_backend: RevocationBackend | None = None


def get_backend() -> RevocationBackend:
    """무효화 백엔드를 고른다. `redis_infra` 규칙을 따른다.

    local/test가 아닌 환경에서 Redis가 없으면 `redis_infra`가 기동을 실패시킨다.
    LRU 대체는 local/test에서만 쓴다.
    주의: Redis가 잠깐 불안정할 때 LRU로 조용히 바꾸지 않는다.
    바꾸면 그 레플리카만 무효화 기록을 보지 못해 로그아웃한 토큰이 그 레플리카에서 통과한다.
    """
    global _backend
    if _backend is not None:
        return _backend
    from api_gateway import redis_infra
    client = redis_infra.get_redis()
    if client is not None:
        _backend = RedisBackend(client)
        log.info("jwt_revocation: RedisBackend (%s)", redis_infra.redis_location())
    else:
        _backend = LRUBackend()  # local/test 전용. 다른 환경은 redis_infra가 기동을 막는다.
    return _backend


def set_backend(backend: RevocationBackend | None) -> None:
    """백엔드를 직접 주입하거나 None으로 초기화한다. 테스트 전용이다."""
    global _backend
    _backend = backend
