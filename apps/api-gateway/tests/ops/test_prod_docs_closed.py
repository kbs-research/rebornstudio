"""운영 환경에서 대화형 API 문서를 닫는지 보는 테스트.

엔드포인트는 모두 인증을 요구하므로 문서가 열려 있어도 인증을 우회할 수는 없다.
다만 비로그인 사용자에게 `/openapi.json`이 관리 API의 형태까지 모두 보여 주므로 운영에서는 닫는다.
"""
import importlib

from api_gateway.config import settings


def _app_with_env(monkeypatch, env: str):
    """지정한 환경으로 앱을 새로 만든다. 라우트 구성은 import 시점에 정해진다."""
    monkeypatch.setattr(settings, "environment", env)
    import api_gateway.main as main
    return importlib.reload(main).app


def _paths(app) -> set[str]:
    """앱에 등록된 라우트 경로 집합을 돌려준다."""
    return {r.path for r in app.routes}


def test_prod_hides_docs(monkeypatch):
    """운영 환경에 문서 경로 세 개가 없는지 확인한다."""
    paths = _paths(_app_with_env(monkeypatch, "prod"))
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert p not in paths, f"운영에 {p}가 열려 있다"


def test_dev_keeps_docs(monkeypatch):
    """개발 환경에서 문서가 열려 있는지 확인한다.

    이 테스트가 실패하면 개발자가 쓰는 도구가 막힌 상태다.
    """
    paths = _paths(_app_with_env(monkeypatch, "dev"))
    for p in ("/docs", "/openapi.json"):
        assert p in paths, f"개발에서 {p}가 닫혔다"


def test_spec_still_generates_in_process(monkeypatch):
    """운영 설정에서도 `app.openapi()`로 계약 생성이 동작하는지 확인한다.

    `gen_openapi`는 HTTP가 아니라 `app.openapi()`를 부른다.
    이 검사가 없으면 운영에서 문서를 닫아 drift 게이트가 깨진 것을 배포 때 처음 알게 된다.
    """
    app = _app_with_env(monkeypatch, "prod")
    spec = app.openapi()
    assert len(spec["paths"]) > 100, "인프로세스 스펙까지 비었다"
    assert "/admin/users/{user_id}/status" in spec["paths"]


def test_restore_module_state(monkeypatch):
    """다른 테스트가 보는 `main` 모듈을 원래 환경으로 되돌린다.

    이 파일이 `reload`로 모듈을 바꿔 놓기 때문이다.
    """
    paths = _paths(_app_with_env(monkeypatch, settings.environment))
    assert "/healthz" in paths
