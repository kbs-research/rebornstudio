# packages

## 목적

앱(`apps/`)과 워커(`workers/`)가 함께 쓰는 라이브러리를 둡니다. 패키지마다 `pyproject.toml`(또는 `package.json`)이 따로 있습니다.
각 패키지는 uv 워크스페이스(루트 `pyproject.toml`의 `[tool.uv.workspace]`)와 pnpm 워크스페이스(`pnpm-workspace.yaml`)의 멤버입니다.
라이선스는 저장소와 같은 Apache-2.0입니다.

## 주요 파일

| 폴더 | 역할 |
|---|---|
| [`reborn-contracts/`](reborn-contracts/README.md) | API 요청·응답 스키마(Pydantic)와 그 스키마에서 생성한 TypeScript 타입, 워커 태스크 이름·큐 표, 화면과 서버가 함께 읽는 판정 표 |
| [`reborn-transcode/`](reborn-transcode/README.md) | DB 없이 실행하는 미디어 처리. FFmpeg 인코딩·probe, 산출 검사, 씬 분할, 비식별화 러너 |
| [`deident/`](deident/README.md) | 영상 얼굴 비식별화 모듈(`essential_deident`). 로컬 파일 하나를 받아 얼굴을 가린 MP4와 좌표 파일을 만듭니다 |

## 설계 이유

- 패키지는 아래쪽에만 의존합니다. `reborn_contracts`는 다른 워크스페이스 패키지를 import하지 않습니다.
  `reborn_transcode`는 `api_gateway`·`worker_io`·`worker_jobs`를 import하지 않습니다.
  루트 `.importlinter`의 `contracts-bottom`·`transcode-standalone` 계약이 CI에서 이 방향을 검사합니다.
  방향이 뒤집히면 잡 이미지에 DB 설정과 도메인 코드가 같이 들어가고, 계약 패키지를 바꿀 때 순환 import가 생깁니다.
- 저장소 접근과 MI 연동은 패키지로 분리하지 않았습니다. 저장소 접근은 `apps/api-gateway/api_gateway/services/media/storage.py` 한 곳에 있습니다.
  MI 분석 결과 회수 작업은 DB 모델에 묶여 있어서 분리하면 순환이 생깁니다.

## 의존성

- 부르는 쪽: `apps/api-gateway`(`reborn_contracts`·`reborn_transcode`), `apps/web`(`@reborn/contracts`), `workers/worker-io`(`reborn_contracts.queues`), `workers/worker-jobs`(세 패키지). `essential_deident`는 `reborn_transcode.deident`가 함수 안에서 import합니다.
- 이미지: `infra/docker/`의 api·worker-io·worker-jobs Dockerfile이 `reborn-contracts`·`reborn-transcode`를 통째로 COPY합니다. `deident`는 비식별화 잡을 받는 이미지(worker-jobs·worker-gpu)에만 통째로 들어가고, 나머지 이미지는 워크스페이스를 읽으려고 `pyproject.toml`만 COPY합니다.

## 남은 일

- 없음.
