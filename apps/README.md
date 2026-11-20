# apps

## 목적

배포할 수 있는 애플리케이션을 둡니다. 하위 폴더 하나가 독립된 배포 단위입니다.
배포 단위마다 이미지, 레플리카 수, 스케일 기준이 따로 있습니다.

## 주요 파일

| 폴더 | 역할 | 스케일 신호 |
|---|---|---|
| `api-gateway/` | FastAPI 백엔드. REST API, OpenAPI 3.1 문서, Celery 작업 등록 | CPU |
| `web/` | React, Vite 6, React Router v7로 만든 단일 페이지 앱. 운영에서는 nginx 이미지가 `/api` 프리픽스를 떼고 서버로 넘기며, 없는 경로는 앱 진입 HTML로 돌려줍니다 | CPU |

워커는 이 폴더에 없습니다. `../workers/`(`worker-io`·`worker-jobs`)가 별도 배포 단위입니다.
워크로드마다 부하가 다르면 스케일 기준도 달라야 하므로 배포 단위를 이렇게 나눴습니다.
운영은 지금 단일 서버 docker compose로 돌고, 클러스터 배포 형상은 두지 않습니다.

## 규칙

- 앱마다 자기 `pyproject.toml`이나 `package.json`이 있습니다. 공통 코드는 `../packages/`의 워크스페이스 의존으로 가져옵니다.
- 컨테이너 이미지는 `../infra/docker/`의 Dockerfile로 빌드합니다. CI는 Dockerfile이나 의존성 파일이 바뀐 PR과 main 푸시에서 이미지 다섯 개(api·worker-io·worker-jobs·web·pgbackup)를 빌드합니다.
- api-gateway는 무상태여야 합니다. 로그인 잠금, rate limit, 토큰 폐기 목록, 캐시는 메모리가 아니라 공유 Redis에 둡니다.
  레플리카마다 기준값이 따로 있으면 보안 동작이 요청을 받은 레플리카에 따라 달라집니다.

## 의존성

`../packages/reborn-contracts`에 Pydantic 스키마가 있습니다. 서버는 이 스키마로 OpenAPI 문서를 만들고, 웹은 그 문서에서 TypeScript 타입을 생성합니다.
화면과 서버가 같은 스키마를 보게 하는 장치는 이것 하나이고, 생성 결과가 어긋나면 CI가 막습니다.
