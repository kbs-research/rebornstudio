"""RebornStudio API Gateway의 FastAPI 앱과 미들웨어·라우터 등록."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi import status as st
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api_gateway import logging_setup
from api_gateway.api.v1 import (
    admin,
    auth,
    connections,
    csp_report,
    dashboard,
    exports,
    maiu,
    projects,
    review,
    setup,
    storage,
    users,
    videos,
    work,
    workforce,
)
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from api_gateway.services import platform_credentials
from api_gateway.services.stacks.connections import InUseError

# 로깅은 모듈을 로드할 때 켠다. lifespan보다 앞서므로 기동 중에 나는 경고도 남는다.
logging_setup.configure()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """기동 때 공유 상태 백엔드를 확인하고 배경 작업을 시작하며, 종료 때 정리한다.

    non-local에서 Redis에 연결할 수 없으면 여기서 기동을 실패시킨다.
    그러면 오케스트레이터가 롤링을 멈추고 구버전이 계속 동작한다.
    이어서 셋업 토큰을 안내하고 연결 정보를 읽는 주기 루프를 시작한다.
    """
    from api_gateway import redis_infra
    redis_infra.ensure_ready()

    # 초기화 전이면 셋업 토큰을 발급해 로그에 남긴다.
    # 이 로그를 읽을 수 있는 사람만 첫 관리자를 만들 수 있다.
    # 공개 IP에서 먼저 접속한 사람이 관리자가 되는 것을 막는 유일한 장치다.
    # 토큰은 기동할 때마다 새로 발급한다. 그래서 이전 기동의 토큰은 재기동하면 무효가 된다.
    try:
        from api_gateway.database import SessionLocal
        from api_gateway.services.auth import setup as setup_service
        async with SessionLocal() as _db:
            if await setup_service.is_uninitialized(_db):
                _t = setup_service.issue_token()
                # 셋업 토큰은 서버 로그를 읽을 수 있는 사람만 첫 관리자가 되게 한다.
                logging.getLogger(__name__).warning(
                    "최초 설정이 필요합니다 — 셋업 토큰: %s",
                    # Redis를 쓰는 환경에서 발급이 실패하면 셋업 API가 막힌다.
                    # 토큰 없이 진행하는 경우는 Redis가 없는 환경뿐이다.
                    _t or ("(발급 실패 — Redis를 확인합니다)" if setup_service.token_required()
                           else "(Redis 없음 — 토큰 없이 진행)"),
                )
    except Exception as _e:  # noqa: BLE001 — 안내 실패가 기동을 막지 않는다
        logging.getLogger(__name__).warning("셋업 상태 확인 실패: %s", _e)

    # 등록한 저장소·분석 연결을 읽어 둔다. 동기 경로(presign 등)는 DB를 조회하지 않고 이 값을 쓴다.
    # 읽지 못해도 기동은 계속한다. 그동안 연결이 필요한 요청은 409를 받는다.
    # 주기 루프가 연결을 다시 읽는다.
    try:
        async with SessionLocal() as _db:
            await platform_credentials.refresh(_db)
    except Exception as _e:  # noqa: BLE001
        logging.getLogger(__name__).warning("연결 읽기 실패: %s", type(_e).__name__)
    import asyncio as _asyncio
    creds_task = _asyncio.create_task(platform_credentials.run_forever())

    try:
        yield
    finally:
        creds_task.cancel()


#: 대화형 API 문서를 여는 환경. `scripts/dev/dev_token.py`와 같은 목록을 쓴다.
#: 둘 다 개발 환경의 범위를 정하므로 두 목록이 달라지면 한쪽에서만 닫힌다.
_DOCS_ENVS = ("local", "dev", "test")

#: 대화형 API 문서(`/docs`·`/openapi.json`)를 열면 True. 운영에서는 닫는다.
#: 엔드포인트는 모두 인증을 요구하지만, 문서가 열려 있으면 비로그인 사용자도
#: 관리 API의 경로와 형태를 볼 수 있다.
#: `scripts/dev/gen_openapi.py`는 HTTP 대신 프로세스 안에서 `app.openapi()`를 부른다.
#: 그래서 문서를 닫아도 `make openapi`와 drift 게이트는 그대로 동작한다.
_docs_open = settings.environment in _DOCS_ENVS

app = FastAPI(
    title="RebornStudio API",
    version="3.1.4",
    description="한국어 보도영상 VLM 학습 데이터 가공 플랫폼",
    openapi_version="3.1.0",
    lifespan=_lifespan,
    docs_url="/docs" if _docs_open else None,
    redoc_url="/redoc" if _docs_open else None,
    openapi_url="/openapi.json" if _docs_open else None,
)


@app.exception_handler(platform_credentials.CredentialsMissingError)
async def _credentials_missing(
    _request: Request, exc: platform_credentials.CredentialsMissingError,
):
    """저장소·분석 자격증명이 없을 때 409와 오류 코드를 돌려준다.

    500이면 화면이 일시 오류로 보고 다시 시도를 안내한다. 이 상태는 분석 연동에서 연결을
    고르거나 자격증명 연결을 고쳐야 풀린다. 그래서 409와 사유 코드를 돌려준다.
    """
    return JSONResponse(status_code=st.HTTP_409_CONFLICT, content={"detail": exc.code})


@app.exception_handler(InUseError)
async def _in_use(_request: Request, exc: InUseError):
    """프로젝트가 쓰고 있어 거절한 연결 변경에 409와 막는 프로젝트 이름을 돌려준다.

    `detail`은 다른 오류와 같은 코드 문자열로 두고 이름은 옆 키에 싣는다. 화면의 오류 사전은
    `detail`만 읽고, 사용 중 안내는 이름으로 먼저 바꿀 프로젝트를 보여 준다.
    """
    return JSONResponse(status_code=exc.status_code, content={
        "detail": exc.detail, "projects": exc.projects, "projects_more": exc.projects_more,
    })


app.add_middleware(RequestContextMiddleware)
# Content-Length 가드. Starlette는 나중에 등록한 미들웨어를 바깥에 둔다.
# 그래서 이 가드는 `RequestContextMiddleware`보다 먼저 요청을 거절한다.
# 가장 바깥은 아래 CORS 미들웨어다.
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    # CSRF 완화: method를 명시한다. wildcard와 credentials를 함께 쓰지 않는다.
    # 스택 저장(분석 대상·저장소 대상)이 PUT이라 PUT도 연다.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
    expose_headers=["X-Request-Id"],
    max_age=600,
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
# 인증 없이 열리는 유일한 쓰기 경로다. 초기화 전에만 동작한다.
app.include_router(setup.router, prefix="/setup", tags=["setup"])
app.include_router(projects.router, prefix="/projects", tags=["projects"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(videos.router)  # videos 라우터는 자체 prefix를 쓴다 (projects/{id}/videos)
app.include_router(work.router)    # /projects/{id}/work/claim, /work/{id}/submit, /work/me
app.include_router(dashboard.router)  # /projects/{id}/dashboard/* (labeler·kpi·health)
app.include_router(review.router)  # /projects/{id}/review/*, /work/{id}/review
app.include_router(exports.router)     # /projects/{id}/exports, /exports/{id}/download
app.include_router(maiu.router)        # /projects/{id}/maiu/dispatch|jobs
app.include_router(workforce.router)   # /projects/{id}/me/decisions·work-history, /me/auth-events
app.include_router(storage.router)        # /projects/{id}/storage/*
app.include_router(connections.router)    # /connections (자격증명 관리)
app.include_router(connections.choices_router)  # /projects/{id}/connection-choices (이름만)
app.include_router(csp_report.router)     # /csp-report (브라우저 CSP 위반 보고, 인증 없음)


@app.get("/healthz", tags=["system"], summary="프로세스 생존 확인 (liveness)")
async def healthz() -> dict[str, str]:
    """liveness 프로브에 응답한다. 프로세스가 살아 있으면 항상 200과 앱 버전을 반환한다.

    인증 없이 부를 수 있다. 의존성(DB·Redis) 확인은 `/readyz`가 한다.
    """
    return {"status": "ok", "version": app.version}


@app.get("/readyz", tags=["system"], summary="필수 의존성(DB·Redis) 연결 확인 (readiness)")
async def readyz() -> dict[str, str]:
    """readiness 프로브에 응답한다. 필수 의존성에 연결할 수 있으면 200을 반환한다.

    인증 없이 부를 수 있다. DB에 연결할 수 없으면 503 `db_unavailable`을 반환한다.
    공유 백엔드가 Redis이면 Redis도 확인하고, 연결할 수 없으면 503 `redis_unavailable`을
    반환한다. local의 인메모리 대체 모드에서는 Redis를 확인하지 않는다.
    자세한 원인은 로그에만 남기고 응답에는 일반 코드만 담는다.
    """
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        logging.getLogger("reborn.access").exception("readyz_db_failed")
        raise HTTPException(st.HTTP_503_SERVICE_UNAVAILABLE, "db_unavailable") from None
    from api_gateway import redis_infra
    try:
        client = redis_infra.get_redis()
        if client is not None:
            client.ping()
    except Exception:  # noqa: BLE001
        logging.getLogger("reborn.access").exception("readyz_redis_failed")
        raise HTTPException(st.HTTP_503_SERVICE_UNAVAILABLE, "redis_unavailable") from None
    return {"status": "ready"}
