# workers

## 목적

Celery 워커 패키지 두 개를 둡니다. worker-io는 DB를 쓰는 주기 작업을 실행하고, DB를 쓰지 않는 작업(이하 잡)의 투입, 회수, 회신 반영을 맡습니다.
worker-jobs는 DB 없이 잡 페이로드 하나로 인코딩, 씬 분할, 재생본, 비식별화를 실행합니다.
두 패키지는 서로를 import하지 않고, 같은 Redis 브로커에서 태스크 이름으로만 이어집니다.

## 주요 파일

| 폴더 | 역할 | 이미지 | compose 서비스 |
|---|---|---|---|
| [`worker-io/`](worker-io/README.md) | beat 스케줄, DB 대상 주기 작업, 잡 투입·회수·회신, 캡션 품질검수 | `infra/docker/worker-io.Dockerfile` | worker(beat 포함) · worker-qc |
| [`worker-jobs/`](worker-jobs/README.md) | 잡 4종(`transcode_one`·`scene_split_one`·`publish_one`·`deident_one`) | `infra/docker/worker-jobs.Dockerfile` | worker-gpu |

## 설계 이유

- 잡에는 DB 설정과 도메인 코드가 필요 없습니다. 잡은 페이로드(presigned URL, 타깃, 펜싱 토큰)만으로 일하고, 하트비트와 완료를 큐로 회신합니다.
  펜싱 토큰은 지금 유효한 시도를 가리키는 토큰입니다. 회신을 받는 쪽은 현재 토큰이 아닌 보고를 낡은 시도의 결과로 보고 버립니다.
- 두 역할이 한 패키지에 있으면 import 한 줄로 경계가 무너지고, 잡 이미지에 `api_gateway`가 들어갑니다.
  패키지를 나누면 루트 `.importlinter`의 `jobs-standalone`·`io-sends-jobs-by-name` 계약이 그 경계를 CI에서 검사합니다.
- 태스크 이름·큐·라우팅 표는 `reborn_contracts.queues` 한 곳에 있고, 두 워커 앱이 같은 표를 읽습니다.
  페이로드 스키마와 멱등 근거는 [`packages/reborn-contracts/queues.md`](../packages/reborn-contracts/queues.md)에 있습니다.
- 태스크 이름은 패키지와 무관하게 `worker_io.tasks.<함수명>`입니다. 큐에 쌓인 메시지, beat 스케줄, 하트비트 Redis 키가 이 이름을 가리킵니다.
  그래서 배포 순서와 무관하게 이전 이미지가 보낸 메시지를 새 워커가 처리합니다.

## 의존성

- worker-io: `api-gateway`(서비스 함수), `reborn-contracts`, `celery[redis]`
- worker-jobs: `reborn-transcode`, `reborn-contracts`, `celery[redis]`
- 컨테이너별 소비 큐, 동시 실행 수, 종료 유예는 `infra/compose/compose.yaml`이 정합니다.

## 실행

```bash
uv run --package worker-io   celery -A worker_io.celery_app   beat   -l info
uv run --package worker-io   celery -A worker_io.celery_app   worker -l info -Q default,q.maiu,q.media,q.qc
uv run --package worker-jobs celery -A worker_jobs.celery_app worker -l info -Q q.transcode,q.deident --prefetch-multiplier 1
```

보통은 `make dev-up`이 compose로 서비스별 워커를 띄웁니다.

## 테스트

```bash
(cd workers/worker-jobs && uv run --package worker-jobs --group test pytest tests/)   # DB·브로커 없음
(cd workers/worker-io   && uv run --package worker-io   --group test pytest tests/)   # Postgres·Redis 필요
```

## 남은 일

- 없음.
