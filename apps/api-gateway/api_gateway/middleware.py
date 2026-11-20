"""요청 ID 발급, 구조화된 액세스 로그, 요청 본문 크기 제한을 맡는 미들웨어."""
import logging
import time
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from api_gateway.config import settings
from api_gateway.logging_setup import actor_var, request_id_var

logger = logging.getLogger("reborn.access")


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Content-Length가 `max_request_body_bytes`를 넘는 요청을 413으로 막는 미들웨어.

    영상은 S3 presigned PUT으로 직접 올리므로 FastAPI를 거치지 않는다.
    그래서 여기서는 JSON 페이로드(기본 2 MiB)만 제한한다.
    Content-Length가 없는 chunked 요청은 매우 드물어서 그대로 통과시킨다.
    필요해지면 스트림 누적 크기로 제한한다.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Content-Length가 한도를 넘으면 413을 반환하고, 아니면 다음 단계로 넘긴다."""
        cl = request.headers.get("content-length")
        limit = settings.max_request_body_bytes
        if cl and cl.isdigit() and int(cl) > limit:
            return JSONResponse(
                status_code=413,
                content={"detail": f"request_body_too_large (max {limit} bytes)"},
            )
        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """X-Request-Id를 발급하거나 받은 값을 이어 쓰고 응답 시간을 로그로 남기는 미들웨어."""

    async def dispatch(self, request: Request, call_next) -> Response:
        """요청에 X-Request-Id를 붙이고 응답 시간을 구조화 로그로 남긴다."""
        rid = request.headers.get("x-request-id") or uuid4().hex[:16]
        request.state.request_id = rid
        # 이 요청 안에서 나는 모든 로그 줄에 요청 ID가 붙는다(`ContextFilter`).
        # 아직 토큰을 해석하기 전이라 actor는 비워 두고 인증 의존성이 채운다.
        rid_token = request_id_var.set(rid)
        actor_token = actor_var.set("")
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "request_failed %s %s elapsed=%.1fms",
                request.method, request.url.path, elapsed_ms,
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "http_elapsed_ms": round(elapsed_ms, 1),
                },
            )
            request_id_var.reset(rid_token)
            actor_var.reset(actor_token)
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = rid
        # 인증 의존성이 `request.state.actor`에 남긴 actor를 액세스 로그에 붙인다
        # (`dependencies`의 `get_current_user` 주석).
        actor_var.set(getattr(request.state, "actor", "") or "")
        # `rid`는 포맷터가 붙이므로 메시지에 넣지 않는다.
        # `extra`의 `http_*` 네 값은 구조화 로그에서 열로 나뉜다.
        # 쿼리스트링은 저장하지 않는다. `?token=…` 같은 값이 로그에 남으면 로그를 읽는 사람이
        # 그 값으로 접근 권한을 얻는다(개인정보를 URL에 싣지 않는다).
        logger.info(
            "%s %s -> %d elapsed=%.1fms",
            request.method, request.url.path, response.status_code, elapsed_ms,
            extra={
                "http_method": request.method,
                "http_path": request.url.path,
                "http_status": response.status_code,
                "http_elapsed_ms": round(elapsed_ms, 1),
            },
        )
        request_id_var.reset(rid_token)
        actor_var.reset(actor_token)
        return response
