"""인증 경로의 요청 한도 제한기.

한 공인 IP 뒤에서 여러 작업자가 함께 일하는 사무실을 기본 조건으로 둔다.
그래서 IP로는 실패한 요청만 세고, 인증이 확인된 요청은 사용자나 로그인 세션으로 센다.
IP로 성공한 요청까지 세면 출근 시각에 사무실 전원의 로그인과 새로고침이 한 버킷을 나눠 쓴다.

버킷:
- `auth:<경로>`: 로그인·재설정 링크 확인·재설정 확정의 실패를 IP로, 비밀번호 변경의 실패를
  사용자로 센다.
  경로마다 버킷이 따로라서 로그인 실패가 몰린 사무실 IP도 재설정 링크를 열 수 있다.
  한도를 넘으면 맞는 비밀번호도 창이 끝날 때까지 429다. 그래야 대입 공격이 성공 응답을 보지 못한다.
- `refresh`: 쿠키가 없거나 무효인 refresh를 IP로 센다. 한도를 넘은 실패는 401 대신 429다.
  유효한 쿠키의 refresh는 이 버킷에 닿지 않는다.
- `refresh_session`: 서명이 확인된 refresh를 로그인 세션(refresh family)으로 모두 센다.
- `invite`: 멤버 초대를 초대하는 사람으로 모두 센다.
계정 단위의 무차별 대입은 `login_throttle`의 (이메일, IP)·이메일 잠금이 따로 막는다.

Redis가 있으면 레플리카가 고정 윈도 카운터를 공유한다.
Redis가 없으면(local/test 한정, redis_infra 규칙) 인메모리 슬라이딩 윈도를 쓴다.
고정 윈도는 경계에서 순간적으로 최대 2배까지 허용하지만, 이 한도의 목적(남용 억제)에는 충분하다.
INCR와 EXPIRE는 한 트랜잭션(MULTI)으로 보낸다. 따로 보내면 사이에 연결이 끊긴 키가
TTL 없이 남아 그 IP를 영구히 막는다.
429에는 창이 끝날 때까지 남은 초를 `Retry-After`로 싣는다. 화면은 그 시간 뒤에 다시 시도한다.

환경 변수(config.settings):
    RATE_LIMIT_AUTH_PER_MIN             기본 10/분, 인증 경로별 실패
    RATE_LIMIT_REFRESH_PER_MIN          기본 300/분, 쿠키가 없거나 무효인 refresh의 IP별 실패
    RATE_LIMIT_REFRESH_SESSION_PER_MIN  기본 60/분, 로그인 세션 하나의 refresh
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import HTTPException, Request, status

from api_gateway import redis_infra
from api_gateway.config import settings
from api_gateway.services import audit

_WINDOW = 60  # 창 길이(초)


def _too_many(name: str, max_per_min: int, retry: int) -> HTTPException:
    """한도 초과 429 예외를 만든다. `retry`는 `Retry-After`에 싣는 초이고 1 이상으로 맞춘다."""
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"rate_limit_exceeded:{name} (max {max_per_min}/min)",
        headers={"Retry-After": str(max(1, retry))},
    )


class SlidingWindowLimiter:
    """키별 60초 슬라이딩 윈도에서 `max_per_min`건까지 허용하는 단일 프로세스 제한기."""

    def __init__(self, *, max_per_min: int, name: str = "rl") -> None:
        """한도(최소 1)와 이름을 정하고 키별 버킷을 만든다."""
        self.max_per_min = max(1, max_per_min)
        self.name = name
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    def _purge(self, key: str, now: float) -> deque[float]:
        """창 밖으로 나간 기록을 지우고 키의 기록을 돌려준다. 잠금 안에서 부른다."""
        q = self._buckets[key]
        while q and q[0] < now - _WINDOW:
            q.popleft()
        return q

    def _wait(self, q: deque[float], now: float) -> int:
        """가장 오래된 기록이 창을 벗어날 때까지 남은 초."""
        return int(q[0] + _WINDOW - now) + 1 if q else 1

    async def hit(self, key: str) -> None:
        """키의 호출을 1회 기록한다. 한도를 넘으면 429 HTTPException을 낸다.

        거절한 호출은 세지 않는다.
        """
        now = time.time()
        async with self._lock:
            q = self._purge(key, now)
            if len(q) >= self.max_per_min:
                raise _too_many(self.name, self.max_per_min, self._wait(q, now))
            q.append(now)

    async def blocked(self, key: str) -> int | None:
        """한도에 닿았으면 다시 받을 때까지 남은 초를, 아니면 None을 돌려준다. 세지 않는다."""
        now = time.time()
        async with self._lock:
            q = self._purge(key, now)
            return self._wait(q, now) if len(q) >= self.max_per_min else None

    async def add(self, key: str) -> int | None:
        """키의 호출을 1회 기록한다. 기록 뒤 한도를 넘었으면 남은 초를, 아니면 None을 돌려준다."""
        now = time.time()
        async with self._lock:
            q = self._purge(key, now)
            q.append(now)
            return self._wait(q, now) if len(q) > self.max_per_min else None


class RedisFixedWindowLimiter:
    """레플리카가 공유하는 고정 윈도 제한기.

    첫 호출부터 키에 60초 TTL을 두고, 한도를 넘으면 429를 낸다.
    async 경로에서 동기 Redis 호출을 쓰는 이유는 redis_infra 모듈 docstring에 있다.
    O(1) 단건 호출이고 드문 경로라서 이벤트 루프에 주는 영향은 무시할 만하다.
    """

    def __init__(self, *, max_per_min: int, name: str, client) -> None:
        """한도(최소 1), 이름, 공유 Redis 클라이언트를 받는다."""
        self.max_per_min = max(1, max_per_min)
        self.name = name
        self.r = client

    def _key(self, key: str) -> str:
        """Redis 키 이름."""
        return f"rl:{self.name}:{key}"

    def _incr(self, key: str) -> int:
        """카운터를 1 올리고 새 값을 돌려준다.

        INCR와 `EXPIRE NX`를 한 트랜잭션으로 보낸다. 윈도는 첫 호출부터 60초이고,
        이전 장애로 TTL 없이 남은 키도 이번 호출에서 TTL을 받는다.
        """
        pipe = self.r.pipeline(transaction=True)
        pipe.incr(self._key(key))
        pipe.expire(self._key(key), _WINDOW, nx=True)
        n, _ = pipe.execute()
        return int(n)

    def _wait(self, key: str) -> int:
        """창이 끝날 때까지 남은 초. TTL을 읽지 못하면 창 길이로 본다."""
        ttl = self.r.ttl(self._key(key))
        return ttl if ttl and ttl > 0 else _WINDOW

    async def hit(self, key: str) -> None:
        """키의 호출을 1회 기록한다. 한도를 넘으면 429 HTTPException을 낸다."""
        if self._incr(key) > self.max_per_min:
            raise _too_many(self.name, self.max_per_min, self._wait(key))

    async def blocked(self, key: str) -> int | None:
        """한도에 닿았으면 창이 끝날 때까지 남은 초를, 아니면 None을 돌려준다. 세지 않는다."""
        n = int(self.r.get(self._key(key)) or 0)
        return self._wait(key) if n >= self.max_per_min else None

    async def add(self, key: str) -> int | None:
        """키의 호출을 1회 기록한다. 기록 뒤 한도를 넘었으면 남은 초를, 아니면 None을 돌려준다."""
        return self._wait(key) if self._incr(key) > self.max_per_min else None


Limiter = SlidingWindowLimiter | RedisFixedWindowLimiter

# 이름별 제한기 싱글톤. 백엔드(공유 Redis 또는 로컬 메모리)는 처음 쓸 때 redis_infra 규칙으로
# 정한다.
_limiters: dict[str, Limiter] = {}


def _get_limiter(name: str, max_per_min: int) -> Limiter:
    """이름별 제한기를 돌려준다. 처음 쓸 때 만들고, 한도는 부를 때마다 받은 값으로 맞춘다.

    Redis가 있으면 공유 고정 윈도를, 없으면 인메모리 슬라이딩 윈도를 쓴다.
    한도를 매번 맞추므로 운영자가 설정을 바꾸거나 테스트가 값을 바꿔도 다음 요청부터 적용된다.
    """
    lim = _limiters.get(name)
    if lim is None:
        client = redis_infra.get_redis()
        lim = (
            RedisFixedWindowLimiter(max_per_min=max_per_min, name=name, client=client)
            if client is not None
            else SlidingWindowLimiter(max_per_min=max_per_min, name=name)
        )
        _limiters[name] = lim
    lim.max_per_min = max(1, int(max_per_min))
    return lim


def _enabled() -> bool:
    """한도를 적용하면 참.

    test 환경은 테스트끼리 같은 주소를 쓰므로 한도를 시험하는 테스트만 켠다.
    """
    return settings.environment != "test"


def _client_key(request: Request) -> str:
    """한도를 셀 키로 클라이언트 주소를 쓴다. 감사 기록의 출처와 같은 규칙이다.

    규칙은 `audit.client_ip`에 있다.
    프록시 뒤에서 `request.client.host`를 그대로 쓰면 nginx 주소 하나 때문에 플랫폼 전체가
    한 버킷이 된다. 서버는 신뢰 프록시의 `X-Forwarded-For`만 반영한다
    (uvicorn `FORWARDED_ALLOW_IPS`). 그래서 헤더를 위조해 한도를 피할 수 없다.
    """
    return audit.client_ip(request) or "unknown"


def ip_key(request: Request) -> str:
    """IP 단위 버킷의 키."""
    return f"ip:{_client_key(request)}"


def user_key(user_id: UUID | str) -> str:
    """사용자 단위 버킷의 키."""
    return f"user:{user_id}"


#: 실패를 세는 인증 경로. 같은 키라도 경로마다 한도를 따로 센다.
AUTH_ENDPOINTS = ("login", "password_change", "reset_check", "reset_confirm")


class FailureTally:
    """`auth_failures` 구간 안에서 예외 없이 끝난 실패를 직접 세는 객체."""

    def __init__(self, lim: Limiter | None, key: str) -> None:
        """제한기(한도를 끈 환경이면 None)와 키를 받는다."""
        self._lim = lim
        self._key = key

    async def fail(self) -> None:
        """실패를 1회 센다. 응답은 바꾸지 않는다."""
        if self._lim is not None:
            await self._lim.add(self._key)


@asynccontextmanager
async def auth_failures(
    endpoint: str, key: str, *, counted: frozenset[int] = frozenset({400, 401}),
) -> AsyncIterator[FailureTally]:
    """인증 경로 하나의 실패만 세는 구간을 연다. 버킷 이름은 `auth:<경로>`다.

    들어갈 때 실패가 한도에 닿아 있으면 처리 없이 429다.
    구간 안에서 `counted` 상태의 HTTPException이 나면 1회 세고 다시 던진다.
    성공한 요청은 세지 않으므로 같은 사무실의 정상 로그인은 서로의 한도를 쓰지 않는다.
    한도를 넘은 뒤에는 맞는 비밀번호도 429라서 대입 공격이 성공 응답으로 비밀번호를 확인할 수 없다.

    Args:
        endpoint: `AUTH_ENDPOINTS`의 경로 이름.
        key: `ip_key` 또는 `user_key`가 만든 키.
        counted: 실패로 셀 상태 코드.

    Raises:
        ValueError: `AUTH_ENDPOINTS`에 없는 경로 이름인 경우.
        HTTPException: 실패가 한도에 닿아 있는 경우
            (429 `rate_limit_exceeded:auth:<경로>`, `Retry-After`).
    """
    if endpoint not in AUTH_ENDPOINTS:
        raise ValueError(endpoint)
    lim = _get_limiter(f"auth:{endpoint}", settings.rate_limit_auth_per_min) if _enabled() else None
    if lim is not None and (wait := await lim.blocked(key)) is not None:
        raise _too_many(lim.name, lim.max_per_min, wait)
    try:
        yield FailureTally(lim, key)
    except HTTPException as e:
        if lim is not None and e.status_code in counted:
            await lim.add(key)
        raise


@asynccontextmanager
async def refresh_failures(request: Request) -> AsyncIterator[None]:
    """`refresh` 버킷으로 실패한 refresh만 IP별로 세는 구간을 연다.

    구간 안에서 401이 나면 1회 세고, 그 실패로 한도를 넘었으면 401 대신 429를 낸다.
    들어갈 때 막지 않는다. 같은 IP에서 무효한 쿠키가 몰려도 유효한 쿠키를 가진 사람은 계속 갱신한다.
    refresh 토큰은 서명된 값이라 대입으로 맞힐 수 없으므로 성공 응답을 숨길 이유가 없다.

    Raises:
        HTTPException: 실패가 한도를 넘은 경우(429 `rate_limit_exceeded:refresh`, `Retry-After`).
    """
    lim = _get_limiter("refresh", settings.rate_limit_refresh_per_min) if _enabled() else None
    key = ip_key(request)
    try:
        yield
    except HTTPException as e:
        if lim is not None and e.status_code == status.HTTP_401_UNAUTHORIZED:
            wait = await lim.add(key)
            if wait is not None:
                raise _too_many(lim.name, lim.max_per_min, wait) from None
        raise


async def refresh_session_hit(family_id: str) -> None:
    """서명이 확인된 refresh를 로그인 세션(refresh family) 단위로 센다. test 환경에서는 건너뛴다.

    부르는 쪽은 토큰 서명을 확인한 뒤에 부른다. 서명 전의 클레임으로 세면 남의 family 이름을 적은
    가짜 토큰으로 그 사람의 한도를 채울 수 있다.

    Raises:
        HTTPException: 한 세션의 갱신이 한도를 넘은 경우(429 `rate_limit_exceeded:refresh_session`).
    """
    if not _enabled():
        return
    await _get_limiter(
        "refresh_session", settings.rate_limit_refresh_session_per_min,
    ).hit(f"fam:{family_id}")


#: 한 사람이 1분에 보낼 수 있는 멤버 초대 수. 초대 응답으로 계정 유무를 대입해 보는 속도를 늦춘다.
#: 사람이 화면에서 한 명씩 넣는 속도로는 닿지 않는 값이다.
INVITE_PER_MIN = 30


async def invite_limit(actor_id: UUID) -> None:
    """멤버 초대 한 건을 초대하는 사람 단위 60초 윈도로 센다. test 환경에서는 건너뛴다.

    초대는 로그인한 관리자만 부르는 경로라서 IP 대신 사람 단위로 센다.
    그래서 같은 사무실의 다른 관리자는 각자의 한도를 쓴다.

    Raises:
        HTTPException: 한도를 넘은 경우(429 `rate_limit_exceeded:invite`, `Retry-After`).
    """
    if not _enabled():
        return
    await _get_limiter("invite", INVITE_PER_MIN).hit(str(actor_id))


def reset_for_test() -> None:
    """제한기 상태를 지우고 싱글톤을 비운다. 테스트 전용이다."""
    for lim in _limiters.values():
        if isinstance(lim, SlidingWindowLimiter):
            lim._buckets.clear()
        else:
            for key in lim.r.scan_iter(match=f"rl:{lim.name}:*", count=500):
                lim.r.delete(key)
    _limiters.clear()
