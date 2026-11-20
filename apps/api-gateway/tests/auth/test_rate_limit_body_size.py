"""요청 한도(rate limiter)와 요청 본문 크기 가드의 단위 테스트."""
import asyncio
import json

import pytest
from api_gateway.config import settings
from api_gateway.services.auth import rate_limit
from fastapi import HTTPException
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def _reset_limiters():
    """테스트 전후로 한도 상태를 초기화한다."""
    rate_limit.reset_for_test()
    yield
    rate_limit.reset_for_test()


# SlidingWindowLimiter 자체

def test_limiter_allows_below_threshold():
    """한도 이하의 요청이 통과하는지 확인한다."""
    lim = rate_limit.SlidingWindowLimiter(max_per_min=3, name="t")

    async def go():
        """같은 키로 한도만큼 hit한다."""
        for _ in range(3):
            await lim.hit("ip1")
    asyncio.run(go())  # 예외 없이 통과한다


def test_limiter_blocks_above_threshold():
    """한도를 넘는 요청이 429인지 확인한다."""
    lim = rate_limit.SlidingWindowLimiter(max_per_min=2, name="t")

    async def go():
        """같은 키로 한도보다 한 번 더 hit한다."""
        await lim.hit("ip1")
        await lim.hit("ip1")
        await lim.hit("ip1")  # 3번째 요청은 429다
    with pytest.raises(HTTPException) as e:
        asyncio.run(go())
    assert e.value.status_code == 429


def test_limiter_keys_are_independent():
    """키마다 한도를 따로 세는지 확인한다."""
    lim = rate_limit.SlidingWindowLimiter(max_per_min=1, name="t")

    async def go():
        """서로 다른 키로 한 번씩 hit한다."""
        await lim.hit("ip1")
        await lim.hit("ip2")  # 다른 IP는 통과한다
    asyncio.run(go())


# test 환경에서 동작하지 않는지 확인

async def test_auth_limiter_noop_in_test_env(client: AsyncClient):
    """test 환경에서는 인증 요청 한도가 동작하지 않는지 확인한다."""
    # test 환경이므로 로그인을 10회 넘게 호출해도 429가 나지 않아야 한다.
    for i in range(15):
        r = await client.post(
            "/auth/login",
            json={"email": f"nobody{i}@example.com", "password": "WrongPass123!"},
        )
        # 401(자격 증명 오류)이나 422(검증 오류)여야 하고 429는 아니어야 한다.
        assert r.status_code != 429, f"iter {i}: rate limited in test env"


# 요청 본문 크기 한도(실제 HTTP 통합)

async def test_body_size_limit_rejects_oversized(
    client: AsyncClient, monkeypatch
):
    """Content-Length가 max_request_body_bytes보다 크면 413인지 확인한다."""
    # 한도를 100B로 줄여서 확인한다.
    monkeypatch.setattr(settings, "max_request_body_bytes", 100)
    huge = json.dumps({"x": "a" * 500})  # 500B를 넘는 payload
    r = await client.post(
        "/auth/login",
        content=huge,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert "request_body_too_large" in r.json()["detail"]


async def test_body_size_limit_allows_small_payload(client: AsyncClient):
    """기본 한도(2MiB) 안의 JSON은 413 없이 통과하는지 확인한다. 인증 실패는 별개다."""
    r = await client.post(
        "/auth/login",
        json={"email": "nobody@example.com", "password": "WrongPass123!"},
    )
    # 413이 아니어야 한다. 401이나 422는 예상한 결과다.
    assert r.status_code != 413
