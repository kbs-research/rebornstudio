"""stats의 30초 캐시 테스트."""
from uuid import UUID

from api_gateway.services.analytics import stats as stats_service
from httpx import AsyncClient
from tests._helpers import (
    new_project,
)


async def test_stats_cache_returns_same_object_within_ttl(
    client: AsyncClient, admin_token: str
):
    """30초 안에 같은 프로젝트를 다시 조회하면 캐시에 등록된 같은 값을 받는지 확인한다."""
    stats_service.reset_cache()
    pid = await new_project(client, admin_token, "cache")
    r1 = await client.get(
        f"/projects/{pid}/stats", headers={"Authorization": f"Bearer {admin_token}"}
    )
    r2 = await client.get(
        f"/projects/{pid}/stats", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()
    # 캐시 등록 여부를 확인한다. 백엔드(Redis·메모리)와 관계없이 조회 도우미로 본다.
    assert stats_service._cache_get(UUID(pid)) is not None


async def test_stats_cache_reset_forces_recompute(
    client: AsyncClient, admin_token: str
):
    """reset_cache를 부르면 캐시가 비워져 다음 조회에서 다시 계산되는지 확인한다."""
    pid = await new_project(client, admin_token, "cache-reset")
    await client.get(
        f"/projects/{pid}/stats", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert stats_service._cache_get(UUID(pid)) is not None
    stats_service.reset_cache()
    assert stats_service._cache_get(UUID(pid)) is None

