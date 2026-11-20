"""MAIU 분석 취소와 Celery 큐 분리 테스트."""
import pytest
import pytest_asyncio
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient


@pytest_asyncio.fixture(loop_scope="session")
async def fresh_client():
    """테스트마다 새 FakeMaiuClient를 주입하고 끝나면 싱글턴을 비운다."""
    fc = FakeMaiuClient()
    maiu_client.set_client(fc)
    yield fc
    maiu_client._client_singleton = None


async def test_fake_cancel_analysis_is_noop(fresh_client: FakeMaiuClient):
    """FakeMaiuClient의 cancel_analysis는 아무 일도 하지 않고 예외 없이 끝난다."""
    await fresh_client.cancel_analysis("anything")  # 예외가 없으면 통과다


@pytest.mark.parametrize(
    ("index_api", "method", "suffix"),
    [
        # 신 경로(기본)는 POST …/index/cancel이다. 신 경로에 DELETE를 보내면 errorCode 300으로 거절된다.
        (True, "POST", "/assets/5678/index/cancel"),
        # 구 경로는 DELETE …/analyze다.
        (False, "DELETE", "/video-assets/5678/analyze"),
    ],
)
async def test_naver_cancel_analysis_path(index_api: bool, method: str, suffix: str):
    """cancel_analysis가 API 모드에 맞는 메서드와 경로로 요청한다. HMAC 호출은 모킹한다.

    신 경로는 POST, 구 경로는 DELETE를 쓴다. 실제 MI API에서 확인한 방식이다.
    """
    from unittest.mock import patch

    from api_gateway.services.maiu.naver_client import NaverMaiuClient

    client = NaverMaiuClient(
        access_key="ak", secret_key="sk",
        workspace="ws", project_id="123", default_bucket="b",
        use_index_api=index_api,
    )
    captured = {}

    async def fake_request(m, path, *, json=None):
        """호출된 메서드와 경로를 기록한다."""
        captured["method"] = m
        captured["path"] = path
        return {}

    with patch.object(client, "_request", side_effect=fake_request):
        await client.cancel_analysis("5678")
    assert captured["method"] == method
    assert captured["path"].endswith(suffix)


async def test_naver_cancel_analysis_swallows_404():
    """404(이미 정리됨)는 무시하고 다른 오류는 예외로 올린다."""
    from unittest.mock import patch

    from api_gateway.services.maiu.naver_client import NaverMaiuClient

    client = NaverMaiuClient(
        access_key="ak", secret_key="sk",
        workspace="ws", project_id="123", default_bucket="b",
    )

    async def fail_404(method, path, *, json=None):
        """MAIU가 404를 돌려준 경우를 흉내 낸다."""
        raise RuntimeError("MAIU DELETE → 404 Not Found")

    with patch.object(client, "_request", side_effect=fail_404):
        await client.cancel_analysis("5678")  # 404는 예외 없이 넘어간다

    async def fail_500(method, path, *, json=None):
        """MAIU가 500을 돌려준 경우를 흉내 낸다."""
        raise RuntimeError("MAIU DELETE → 500 Internal Error")

    with (
        patch.object(client, "_request", side_effect=fail_500),
        pytest.raises(RuntimeError, match="500"),
    ):
        await client.cancel_analysis("5678")


def test_celery_task_routes_isolates_maiu():
    """q.maiu 큐에는 run_maiu_reconciler만 가고, 라우팅 표에 없는 태스크는 default 큐로 간다.

    worker_io는 별도 워크스페이스 패키지이고 api-gateway는 이 패키지에 의존하지 않는다(import-linter 계약).
    그래서 `uv run --package api-gateway`로 이 패키지만 설치한 환경에서는 import가 되지 않아 skip한다.
    `uv sync --all-packages`로 설치한 환경(CI 포함)에서는 이 테스트를 실행한다.
    q.maiu 격리는 worker-io 자체 테스트에 없으므로 이 테스트가 유일한 검증이다.
    """
    celery_app = pytest.importorskip("worker_io.celery_app")
    routes = celery_app.app.conf.task_routes
    on_maiu = sorted(name for name, r in routes.items() if r.get("queue") == "q.maiu")
    assert on_maiu == ["worker_io.tasks.run_maiu_reconciler"]
    # 라우팅 표에 없는 beat 태스크를 실제 라우터로 풀어 기본 큐로 가는지 본다.
    unrouted = "worker_io.tasks.reclaim_expired_work_items"
    assert unrouted not in routes
    router = celery_app.app.amqp.router
    assert router.route({}, unrouted)["queue"].name == "default"
    assert router.route({}, on_maiu[0])["queue"].name == "q.maiu"
