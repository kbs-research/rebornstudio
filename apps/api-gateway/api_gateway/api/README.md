# api

HTTP 라우터 패키지입니다. 버전마다 하위 폴더를 둡니다. 지금은 `v1/` 하나입니다.
라우터 목록과 권한 규칙은 `v1/README.md`에 있습니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `__init__.py` | 패키지 표시 |
| `v1/` | v1 라우터. 도메인마다 모듈 하나를 두고, 영상 라우터만 하위 패키지(`v1/videos/`)로 둡니다 |

## 설계 이유

- 폴더 이름 `v1`은 모듈을 묶는 용도입니다. URL에는 버전 접두사가 없습니다(`/projects/{project_id}/…`). 화면과의 계약은 OpenAPI 문서(`docs/api/openapi.json`)와 그 문서에서 생성한 TypeScript 타입(`packages/reborn-contracts/typescript/src/api.ts`)으로 맞춥니다. 두 파일이 코드와 다르면 CI가 실패합니다.
- 라우터는 `api_gateway.services`와 `api_gateway.models`만 import합니다. 레이어는 `api`, `services`, `models` 순서로만 의존하고, `pyproject.toml`의 import-linter 계약이 이 방향을 검사합니다.

## 의존성

- 부르는 것: `api_gateway.services`, `api_gateway.dependencies`, `reborn_contracts`
- 부르는 쪽: `api_gateway/main.py`

## 남은 일

- 없음.
