"""레플리카들이 상태를 공유하는 Redis 클라이언트.

레플리카 N개가 공유 상태를 두는 곳은 이 클라이언트 하나뿐이다.
로그인 잠금·rate limit·JWT revocation·stats 캐시를 각 프로세스 안에 두면
LB가 요청을 나누므로 보안 임계가 사실상 N배로 느슨해진다.
그래서 공유해야 할 상태는 모두 이 모듈이 주는 클라이언트로 저장한다.

non-local에서 Redis에 연결할 수 없으면 기동을 실패시킨다.
인메모리로 대체하면 로그아웃한 토큰이 레플리카에 따라 계속 유효해서
보안 동작이 요청마다 달라진다.
local/test에서만 None을 돌려주고 호출자가 인메모리로 대체한다.

동기 클라이언트를 쓴다. 대상 연산은 모두 O(1) 단건이고 호출 경로는 저빈도(인증·30초 캐시)다.
그래서 LAN Redis의 1ms 미만 블로킹은 이벤트 루프에 영향이 거의 없다.
`jwt_revocation`도 같은 방식이다. 고빈도 경로에 쓰게 되면 redis.asyncio로 바꾼다.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

from api_gateway.config import settings

log = logging.getLogger(__name__)

# 프로세스 안에서 한 번 판정한 공유 클라이언트. local/test에서 Redis가 없으면 None이다.
_client = None
# 판정을 마쳤으면 True.
_resolved = False


def redis_location(url: str | None = None) -> str:
    """Redis 주소에서 호스트·포트·DB 번호만 뽑아 로그용 문자열로 만든다.

    REDIS_URL에는 사용자 이름과 비밀번호가 들어갈 수 있다. 주소 전체를 로그에 남기면
    운영 로그를 읽는 사람이 그 비밀번호로 Redis에 접속한다.
    """
    parts = urlsplit(url if url is not None else settings.redis_url)
    db = parts.path.lstrip("/") or "0"
    return f"{parts.hostname}:{parts.port or 6379}/{db}"


def get_redis():
    """공유 Redis 클라이언트를 반환한다.

    첫 호출에서 ping으로 판정하고 결과를 캐시한다.
    실행 중에 백엔드가 바뀌면 로그인 잠금 같은 상태가 생겼다 사라졌다 하므로 처음 판정으로 고정한다.

    Returns:
        Redis 클라이언트. local/test에서 연결할 수 없으면 None.

    Raises:
        RuntimeError: local/test가 아닌 환경에서 Redis에 연결할 수 없는 경우.
    """
    global _client, _resolved
    if _resolved:
        return _client
    try:
        import redis
        client = redis.from_url(settings.redis_url, socket_timeout=2, decode_responses=True)
        client.ping()
        _client = client
        log.info("redis_infra: 공유 상태 백엔드 = Redis (%s)", redis_location())
    except Exception as e:  # noqa: BLE001
        if settings.environment not in ("local", "test"):
            raise RuntimeError(
                f"Redis 연결 실패({e}) — non-local에서 인메모리 강등은 금지다. "
                "REDIS_URL을 확인하라."
            ) from e
        log.warning("redis_infra: Redis 없음(%s) → 인메모리 fallback (local/test 전용)", e)
        _client = None
    _resolved = True
    return _client


def ensure_ready() -> None:
    """기동할 때 Redis 연결을 미리 판정한다. `main.py`의 lifespan에서 부른다.

    non-local에서 Redis에 연결할 수 없으면 여기서 기동을 실패시킨다.
    그러면 오케스트레이터가 롤링을 멈추고 구버전이 계속 동작한다.
    """
    get_redis()


def reset_for_test() -> None:
    """판정 캐시를 비운다. 테스트가 부른다. 다음 `get_redis` 호출은 다시 연결을 시도한다."""
    global _client, _resolved
    _client = None
    _resolved = False
