# api_gateway

FastAPI 앱 패키지입니다. 이 폴더 바로 아래에는 모든 도메인이 함께 쓰는 모듈이 있습니다. 앱 생성, 미들웨어·라우터 등록, 설정, DB 세션, 인증 의존성, 로깅이 여기에 속합니다.
업무 로직은 `services/`, HTTP 경로는 `api/v1/`, 테이블 정의는 `models/`에 있습니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `main.py` | 앱 생성, 미들웨어(CORS·본문 크기 제한·요청 컨텍스트)와 라우터 등록, `/healthz`·`/readyz`, lifespan |
| `config.py` | 환경변수 설정(`Settings`). local·test가 아닌 환경에서 비밀값과 CORS를 검사하는 `_enforce_prod_secrets` |
| `database.py` | async 엔진과 세션 팩토리(`SessionLocal`). 풀 크기는 환경변수로 정합니다 |
| `dependencies.py` | 라우트 의존성. `get_signed_in_user`(로그인만 확인), `get_current_user`(개인정보 동의까지 확인, 업무 API 기본값), `require_super_admin` |
| `security.py` | 비밀번호 해시(bcrypt, SHA-256 사전 해시), JWT 발급·검증, 토큰 폐기, 비밀번호 재설정 비밀값 |
| `middleware.py` | `RequestContextMiddleware`(요청 ID·액세스 로그), `BodySizeLimitMiddleware`(JSON 본문 413) |
| `logging_setup.py` | 로깅 설정. 요청 ID와 사용자 ID를 `contextvars`로 모든 로그 줄에 붙입니다 |
| `redis_infra.py` | 레플리카가 함께 쓰는 Redis 클라이언트. local이 아닌 환경에서 연결할 수 없으면 기동을 실패시킵니다 |
| `api/` | HTTP 라우터. 목록은 `api/v1/README.md`에 있습니다 |
| `services/` | 도메인 로직. 경계와 쓰기 소유권은 `services/README.md`에 있습니다 |
| `models/` | SQLAlchemy ORM 모델. 목록은 `models/README.md`에 있습니다 |
| `cli/` | 운영자가 서버에서 실행하는 관리 명령(최고관리자 비밀번호 복구). 목록은 `cli/README.md`에 있습니다 |

## 설계 이유

- local·test가 아닌 환경에서 JWT 비밀값이나 연결 암호화 키가 비었거나, 기본값이거나, 32자 미만이면 `config.py`가 기동을 막습니다. 기본값으로 운영 환경이 뜨면 누구나 토큰을 위조할 수 있기 때문입니다.
- 로그인 잠금, rate limit, 토큰 폐기 목록은 `redis_infra.py`의 Redis에 둡니다. 프로세스 메모리에 두면 레플리카마다 상태가 달라집니다. 그러면 로그아웃한 토큰이 다른 레플리카에서 계속 통과합니다.
- 대화형 API 문서(`/docs`·`/openapi.json`)는 local·dev·test에서만 엽니다. `make openapi`는 프로세스 안에서 `app.openapi()`를 부르므로, 운영에서 문서를 닫아도 계약 생성은 동작합니다.
- 저장소·분석 자격증명이 없으면 서비스가 `CredentialsMissingError`를 던집니다. `main.py`는 이 오류를 409와 사유 코드로 바꿉니다. 500으로 응답하면 화면이 일시 오류로 판단해 재시도를 안내하기 때문입니다.
- 라우트는 요청 검증과 서비스 호출만 합니다. 커밋은 서비스가 합니다. 감사 이벤트(`services/audit.emit`)는 커밋 전에 같은 트랜잭션에 넣습니다. 그래서 업무 변경과 감사 기록이 함께 저장되거나 함께 취소됩니다.
- 오류 응답의 `detail`은 snake_case 코드입니다. 화면은 `apps/web/src/shared/api/error_text.ts` 사전으로 이 코드를 문구로 바꿉니다. 사전에 없는 코드는 `scripts/ci/check-error-codes.py`가 찾아냅니다.

## 의존성

- 부르는 것: `reborn-contracts`(스키마), PostgreSQL(asyncpg), Redis, `services/platform_credentials`(기동할 때 연결 정보를 읽습니다)
- 부르는 쪽: uvicorn(`api_gateway.main:app`), `workers/worker-io`(`services`와 `database`를 import), `alembic/env.py`(`models.Base`)
