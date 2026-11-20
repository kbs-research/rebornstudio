"""API 프로세스의 작은 보안·안정성 설정을 보는 테스트.

스택 저장은 PUT이라 CORS 허용 메서드에 PUT이 없으면 별도 오리진 화면에서 저장이 막힌다.
DB가 재시작하면 풀에 남은 끊긴 커넥션을 첫 요청이 꺼내 500을 받는다.
"""
from api_gateway.config import settings
from api_gateway.database import engine
from httpx import AsyncClient


async def test_cors_preflight_allows_put(client: AsyncClient):
    """허용한 오리진의 PUT 사전 요청이 통과하는지 확인한다."""
    origin = settings.cors_origins_list[0]
    r = await client.options("/projects/x/analysis-stack", headers={
        "Origin": origin, "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "Authorization, Content-Type",
    })
    assert r.status_code == 200, r.text
    assert "PUT" in r.headers["access-control-allow-methods"]


def test_api_engine_checks_connections_before_use():
    """API 엔진이 풀에서 꺼낸 커넥션을 쓰기 전에 살아 있는지 확인하는지 본다."""
    assert engine.pool._pre_ping is True
