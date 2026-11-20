# apps/api-gateway

FastAPI REST 서버입니다. 화면(web)이 부르는 백엔드는 이 서버 하나입니다. 워커가 처리할 잡도 이 서버가 큐에 넣습니다.
API 계약은 OpenAPI 3.1입니다. `make openapi`가 `docs/api/openapi.json`을 갱신하고, 코드와 파일이 다르면 CI가 실패합니다.
엔드포인트 수는 여기에 적지 않습니다. 수가 바뀔 때마다 문서가 낡기 때문입니다. 저장소 루트에서 아래 명령으로 셉니다.

```bash
python3 -c "import json;d=json.load(open('docs/api/openapi.json'));print(sum(m in('get','post','put','patch','delete') for p in d['paths'].values() for m in p))"
```

## 주요 파일

| 경로 | 역할 |
|---|---|
| `api_gateway/` | 앱 패키지. 라우터(`api/v1/`), 도메인 로직(`services/`), ORM 모델(`models/`), 설정·보안 모듈 |
| `alembic/` | PostgreSQL 스키마 마이그레이션 |
| `tests/` | 서버 pytest. 도메인별 폴더 구성과 테스트 DB 규칙은 `tests/README.md`에 있습니다 |
| `pyproject.toml` | 의존성, pytest 설정, 도메인 경계 계약(`[tool.importlinter]`) |
| `alembic.ini` | Alembic 설정. 기본 접속 주소가 없고 `DATABASE_URL` 환경변수로만 받습니다 |

## 설계 이유

- 라우터는 요청 검증과 서비스 호출만 합니다. 판정은 `services/`에 둡니다. 라우터에 판정이 있으면 워커와 배치가 같은 판정을 다시 구현하게 되고, 두 구현의 결과가 달라집니다.
- 도메인 간 import는 `pyproject.toml`의 `[tool.importlinter]` 허용 목록에 있는 것만 통과합니다. 새 교차 import를 쓰면 같은 커밋에서 허용 목록과 `api_gateway/services/README.md`의 소유권 표를 함께 고칩니다. 목록을 고치지 않으면 CI의 `lint-imports`가 실패합니다.
- 설정은 실행 위치와 관계없이 저장소 루트의 `.env`를 읽습니다(`api_gateway/config.py`). 다른 파일은 `REBORN_ENV_FILE`로 지정하고, 빈 값이면 `.env`를 읽지 않습니다. 테스트 conftest는 빈 값을 넣어 로컬 `.env`가 테스트에 섞이지 않게 합니다.
- 스키마 변경은 expand-contract 방식을 따릅니다. 롤링 배포 중에는 구버전 코드가 새 스키마에서 동작합니다. 그래서 컬럼 삭제는 그 컬럼을 읽지 않는 코드가 배포된 뒤에만 합니다.

## 로컬 실행

기본 실행 방법은 compose(`make dev-up`)입니다. api 컨테이너는 소스를 마운트하고 `--reload`로 실행되므로 파일을 저장하면 바로 반영됩니다. 이미지는 `pyproject.toml`이나 `uv.lock`을 바꿨을 때만 다시 빌드합니다. 호스트에서 직접 띄울 때는 아래 명령을 씁니다.

```bash
uv run --package api-gateway uvicorn api_gateway.main:app --reload --port 8000
```

## 마이그레이션

```bash
cd apps/api-gateway && DATABASE_URL="postgresql+asyncpg://reborn:reborn@localhost:5433/rebornstudio" \
  uv run --package api-gateway alembic upgrade head
```

pytest가 새 스키마를 쓰려면 테스트 DB에도 따로 적용해야 합니다. 작성 규칙은 `alembic/README.md`에 있습니다.

## 의존성

- 외부: FastAPI, SQLAlchemy(async)와 asyncpg, Alembic(psycopg), pydantic-settings, boto3, redis, PyJWT, bcrypt, cryptography(Fernet), httpx
- 내부 패키지: `reborn-contracts`(요청·응답 스키마와 큐 계약), `reborn-transcode`(변환 사양)
- 부르는 쪽: `apps/web`(HTTP), `workers/worker-io`(이 패키지의 `services`를 import해 DB 작업을 실행)
