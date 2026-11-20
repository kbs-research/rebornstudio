"""로그인 확인을 거치지 않는 라우트가 모두 이유와 함께 표에 있는지 확인한다.

업무 라우트에 인증 의존성을 빠뜨리면 누구나 부를 수 있는데, 응답이 정상이라 화면으로는 드러나지 않는다.
앱의 라우트마다 의존성 나무를 따라가 `get_signed_in_user`가 없으면 인증 없는 라우트로 보고,
일부러 연 라우트의 표(`PUBLIC_ROUTES`)와 맞춘다.
"""
from api_gateway import dependencies
from api_gateway.main import app
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

#: 일부러 인증 없이 연 라우트와 그 이유. 라우트를 더하거나 인증을 걸면 이 표도 고친다.
PUBLIC_ROUTES: dict[str, str] = {
    "GET /healthz": "인프라 프로브(liveness). 앱 버전만 돌려준다",
    "GET /readyz": "인프라 프로브(readiness). 상태 문구만 돌려준다",
    "POST /auth/login": "로그인. 주소별 한도와 이메일 단위 잠금으로 막는다",
    "POST /auth/password/reset/check": "재설정 링크 상태 확인. 링크의 토큰이 곧 자격이다",
    "POST /auth/password/reset/confirm": "재설정 확정. 링크의 토큰이 곧 자격이다",
    "POST /auth/refresh": "토큰 갱신. refresh 토큰이 곧 자격이고 주소별 한도로 막는다",
    "POST /auth/logout": "로그아웃. refresh 토큰으로 그 로그인을 끊는다",
    "GET /setup/status": "최초 설정 여부. 첫 관리자가 없을 때 화면이 부른다",
    "POST /setup/complete": "첫 super_admin 생성. 서버 로그의 셋업 토큰이 자격이고 초기화 뒤에는 거절한다",
    "POST /csp-report": (
        "브라우저 CSP 위반 보고 수집. 브라우저가 로그인 정보 없이 보낸다. 주소별 한도와 본문 64KiB 상한으로 "
        "막고, 운영 로그에 막힌 주소의 출처와 화면 경로만 남긴다"
    ),
}


def _calls(dep: Dependant):
    """의존성 나무의 모든 호출 대상을 내놓는다."""
    yield dep.call
    for sub in dep.dependencies:
        yield from _calls(sub)


def _public() -> set[str]:
    """로그인 확인 의존성이 없는 라우트를 `METHOD 경로` 꼴로 돌려준다."""
    out = set()
    for r in app.routes:
        if isinstance(r, APIRoute) and dependencies.get_signed_in_user not in set(_calls(r.dependant)):
            out |= {f"{m} {r.path}" for m in r.methods}
    return out


def test_every_public_route_is_listed_with_reason():
    """인증 없는 라우트가 모두 표에 있고, 표에 인증 있는 라우트나 없는 라우트가 남지 않았는지 확인한다."""
    public = _public()
    missing = sorted(public - PUBLIC_ROUTES.keys())
    stale = sorted(PUBLIC_ROUTES.keys() - public)
    assert not missing, f"인증 없는 라우트가 표(PUBLIC_ROUTES)에 없음: {missing}"
    assert not stale, f"인증이 있거나 앱에 없는 라우트가 표에 남아 있음: {stale}"
    assert all(reason.strip() for reason in PUBLIC_ROUTES.values())
