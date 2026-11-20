# reborn-contracts/python

## 목적

`reborn-contracts` 계약의 Python 패키지입니다. 서버(`apps/api-gateway`)가 요청·응답 모델로 import하고, FastAPI가 이 모델로 OpenAPI 3.1 명세를 만듭니다.
워커는 이 패키지의 `queues` 모듈로 태스크 이름과 큐를 맞춥니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `pyproject.toml` | 패키지 정의. 의존성은 `pydantic[email]` 하나이고 빌드는 hatchling |
| [`src/reborn_contracts/`](src/reborn_contracts/README.md) | 도메인별 스키마 모듈과 `queues` 표. 모듈 목록은 그 폴더 README에 있습니다 |

## 설계 이유

- 외부 의존성을 Pydantic 하나로 둡니다. 서버, 워커, 생성 스크립트가 모두 이 패키지를 설치하므로 의존성이 늘면 모든 이미지가 함께 커집니다.
- 이 패키지의 모델이 API 계약의 원본입니다. 모델을 고치면 `make openapi`와 `make contracts`를 실행합니다.
  그리고 `docs/api/openapi.json`과 `../typescript/src/api.ts`를 같은 커밋에 넣습니다. CI가 두 파일의 차이를 검사합니다.

## 의존성

- 이 패키지가 부르는 것: `pydantic`
- 부르는 쪽: `apps/api-gateway`, `workers/worker-io`, `workers/worker-jobs`, `scripts/dev/gen_openapi.py`(서버 앱을 거쳐)
- 루트 `.importlinter`의 `contracts-bottom` 계약이 이 패키지에서 다른 워크스페이스 패키지로 가는 import를 막습니다.

## 남은 일

- 없음.
