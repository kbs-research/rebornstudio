"""refresh 토큰을 담는 HttpOnly 쿠키와 그 쿠키를 읽는 경로의 출처 확인.

refresh 토큰은 응답 본문에 싣지 않고 쿠키로만 준다. 화면 스크립트가 읽을 수 없으므로
XSS가 생겨도 refresh 토큰은 나가지 않는다.

쿠키 속성:
- `HttpOnly`: 스크립트가 읽지 못한다.
- `SameSite=Strict`: 다른 사이트에서 시작한 요청에는 붙지 않는다.
- `Path=/api/auth`: 브라우저가 보는 경로 기준이다. 웹 nginx와 개발 서버(vite)가 `/api` 접두를
  떼고 api로 넘기므로 api 라우트(`/auth/refresh`)가 아니라 브라우저 주소(`/api/auth/refresh`)에
  맞춘다. 업무 API(`/api/projects` 등)에는 붙지 않는다.
- `Secure`: 요청이 https로 왔을 때만 붙인다. 판정은 uvicorn `--proxy-headers`가 신뢰 프록시
  (`FORWARDED_ALLOW_IPS`)의 `X-Forwarded-Proto`를 반영한 요청 scheme으로 한다. 기본 배포는 TLS 없이
  80을 여는 구성이라, 환경 이름으로 `Secure`를 붙이면 평문 http에서 브라우저가 쿠키를 저장하지 않아
  새로고침마다 로그인이 풀린다. 평문 http에서는 이 쿠키도 다른 요청처럼 평문으로 오가므로
  운영은 앞단에 TLS를 둔다(`docs/install.md`의 TLS 절).
- `Max-Age`: refresh 토큰의 만료까지다. 토큰 만료가 세션 끝(로그인 시각과 절대 수명의 합)을
  넘지 않으므로 쿠키도 세션 끝을 넘지 않는다.

쿠키가 자동으로 붙는 경로는 refresh와 logout뿐이다. 두 경로는 `Origin`(없으면 `Referer`)이
허용 출처가 아니면 403 `origin_not_allowed`로 거절한다(`require_same_origin`). 그 밖의 API는
Authorization 헤더의 access 토큰만 보므로 다른 사이트가 요청을 위조해도 인증되지 않는다.
"""
from datetime import UTC, datetime
from urllib.parse import urlsplit

import jwt
from fastapi import HTTPException, Request, Response, status

from api_gateway.config import settings

#: refresh 토큰 쿠키 이름.
#: `__Host-`·`__Secure-` 접두는 `Secure`가 없는 평문 http 배포와 `Path` 한정에 맞지 않아
#: 쓰지 않는다.
REFRESH_COOKIE = "reborn_refresh"

#: 쿠키 경로. 브라우저가 보는 refresh·logout 주소의 공통 접두다.
REFRESH_COOKIE_PATH = "/api/auth"


def cookie_secure(request: Request) -> bool:
    """refresh 쿠키에 `Secure`를 붙여야 하면 True를 반환한다. 요청 scheme이 https일 때만 참이다.

    scheme은 uvicorn이 신뢰 프록시의 `X-Forwarded-Proto`를 반영한 값이다. 신뢰 대역 밖에서 온 헤더는
    반영되지 않으므로 클라이언트가 헤더를 바꿔 실어도 판정이 바뀌지 않는다.
    """
    return request.url.scheme == "https"


def _token_max_age(token: str) -> int:
    """방금 발급한 refresh 토큰의 남은 수명(초)을 돌려준다. 이미 지났으면 0이다.

    서명은 이 서버가 방금 했으므로 다시 검증하지 않고 `exp`만 읽는다.
    """
    exp = jwt.decode(token, options={"verify_signature": False})["exp"]
    return max(0, int(exp - datetime.now(UTC).timestamp()))


def set_refresh_cookie(request: Request, response: Response, token: str) -> None:
    """응답에 refresh 토큰 쿠키를 싣는다. 로그인과 refresh 교체가 부른다."""
    response.set_cookie(
        REFRESH_COOKIE, token,
        max_age=_token_max_age(token),
        path=REFRESH_COOKIE_PATH,
        secure=cookie_secure(request),
        httponly=True,
        samesite="strict",
    )


def clear_refresh_cookie(request: Request, response: Response) -> None:
    """응답에 refresh 쿠키 삭제를 싣는다. 로그아웃이 부른다.

    refresh 실패(401)에서는 지우지 않는다. 여러 탭이 거의 동시에 갱신하면 교체에 실패한 요청의
    401 응답이 다른 탭이 받은 새 쿠키를 지워 모든 탭의 세션이 끊긴다.
    """
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH,
        secure=cookie_secure(request), httponly=True, samesite="strict",
    )


def read_refresh_token(request: Request, body_token: str | None) -> tuple[str | None, str]:
    """요청의 refresh 토큰과 그 출처(`cookie`·`body`·`none`)를 돌려준다. 쿠키를 먼저 본다.

    본문 토큰은 브라우저 저장소에 refresh 토큰이 남아 있는 사용자를 위한 호환 경로다.
    한 번 교환하면 쿠키가 생기고 화면은 저장소의 토큰을 지운다.
    """
    cookie = request.cookies.get(REFRESH_COOKIE)
    if cookie:
        return cookie, "cookie"
    if body_token:
        return body_token, "body"
    return None, "none"


def _origin(url: str) -> str | None:
    """주소에서 `scheme://host[:port]` 출처를 뽑는다. 기본 포트는 생략하고 http(s) 밖은 None이다."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{host}" if port in (None, default) else f"{scheme}://{host}:{port}"


def _self_origin(request: Request) -> str | None:
    """요청을 받은 서버 자신의 출처(요청 scheme과 `Host` 헤더)를 돌려준다.

    웹 nginx는 `Host`를 그대로 넘기고, uvicorn은 신뢰 프록시의 `X-Forwarded-Proto`를
    scheme에 반영한다. 그래서 앞단이 두 값을 바르게 넘기면 브라우저가 본 주소와 같다.
    다른 사이트의 페이지는 브라우저 요청의 두 값을 바꿀 수 없으므로 출처 비교에 쓸 수 있다.
    """
    host = request.headers.get("host")
    if not host:
        return None
    return _origin(f"{request.url.scheme}://{host}")


def allowed_origins(request: Request) -> set[str]:
    """refresh·logout을 허용하는 출처 집합(설정의 CORS 출처와 서버 자신의 출처)을 돌려준다."""
    out = {o for o in (_origin(x) for x in settings.cors_origins_list) if o}
    own = _self_origin(request)
    if own:
        out.add(own)
    return out


async def require_same_origin(request: Request) -> None:
    """요청 출처가 허용 출처가 아니면 403으로 거절한다. refresh·logout 라우트의 의존성이다.

    `Origin`을 먼저 보고 없으면 `Referer`의 출처를 본다. 둘 다 없으면 거절한다.
    브라우저는 POST에 `Origin`을 붙이므로 둘 다 없는 요청은 브라우저 밖의 클라이언트다.

    Raises:
        HTTPException: 출처가 없거나 허용 출처가 아닌 경우(403 `origin_not_allowed`).
    """
    source = request.headers.get("origin")
    if source is None:
        source = request.headers.get("referer")
    if not source or _origin(source) not in allowed_origins(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "origin_not_allowed")
