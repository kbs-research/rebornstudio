# worker-jobs

## 목적

DB 없이 실행하는 잡 워커 패키지입니다. 잡 페이로드(presigned URL, 타깃, 펜싱 토큰) 하나로 인코딩, 씬 분할, 재생본, 비식별화를 실행합니다.
하트비트와 완료는 큐 메시지로 회신합니다. 펜싱 토큰은 현재 시도가 아닌 보고를 버리는 데 쓰는 토큰입니다.
상태 전이, 펜싱 토큰 확인, 리스, 재시도 판정은 회신을 받는 worker-io 한 곳에서 합니다. worker-io의 `worker_io.tasks.callbacks`가 `api_gateway.services.media.*`를 불러 판정합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `pyproject.toml` | 패키지 정의. 의존성은 `celery[redis]`·`reborn-transcode`·`reborn-contracts` |
| [`worker_jobs/`](worker_jobs/README.md) | Celery 앱과 잡 태스크 4종 |
| [`tests/`](tests/README.md) | 등록 이름·재전달 옵션·라우팅·회신 계약 테스트 |

## 설계 이유

- `api_gateway`·`worker_io`를 import하지 않습니다. 루트 `.importlinter`의 `jobs-standalone` 계약이 이 import를 막습니다.
  그래야 잡 이미지에 DB 설정과 도메인 코드가 들어가지 않고, 잡을 다른 이미지, 언어, GPU 노드로 옮길 수 있습니다.
- 잡 태스크는 모두 `acks_late=True`, `reject_on_worker_lost=False`, `ignore_result=True`입니다. 워커 컨테이너가 강제 종료되면 확인 응답이 없는 메시지가 재전달 시간 뒤에 다시 전달됩니다.
  실행 자식만 죽은 메시지는 다시 넣지 않습니다. 다시 넣으면 자식을 죽이는 입력(메모리 초과 등)이 같은 토큰으로 곧바로 다시 돌고, 하트비트가 리스를 이어 가 회수의 시도 상한이 동작하지 않습니다.
  리스가 만료되면 worker-io의 `reclaim_expired_*`가 새 토큰으로 되돌리고, 시작한 뒤 회수된 시도는 시도로 세어 상한에서 실패로 확정합니다. 중복 실행의 결과는 받는 쪽의 펜싱 토큰 확인이 버립니다.
- 브로커 재전달 시간(`visibility_timeout`)은 worker-io 앱과 같은 24시간입니다(`reborn_contracts.queues.BROKER_VISIBILITY_TIMEOUT_S`). 잡 URL 유효 기간보다 길어서 실행 중인 잡이 다시 전달되지 않습니다.
- 러너가 어떤 예외로 끝나도 완료 보고를 보냅니다. 예상하지 못한 예외의 사유는 `UNEXPECTED_ERROR`입니다. 보고가 없으면 리스가 만료될 때까지 대상이 묶입니다.
- 일하기 전에 시작 하트비트를 보냅니다. 투입할 때 건 리스는 큐 대기 시간을 덮을 만큼 길고, 첫 하트비트가 리스를 평소 길이(5분)로 줄입니다.
  그래서 시작한 뒤 중단된 잡은 몇 분 안에 회수됩니다. 회수된 시도의 메시지는 worker-io가 취소 방송을 보내므로, 이 워커는 그 메시지를 시작하지 않고 버립니다.
  취소에는 Celery `revoke`를 쓰므로 원격 제어가 켜져 있어야 합니다. 기본값은 켜짐입니다.

## 의존성

- `reborn-transcode`: 인코딩·씬 분할·비식별화 러너(ffmpeg·ffprobe·httpx)
- `reborn-contracts`: 태스크 이름·큐·라우팅 표(`reborn_contracts.queues`)
- `celery[redis]`: 브로커는 worker-io와 같은 Redis(`REDIS_URL`)입니다.
- 이미지는 `infra/docker/worker-jobs.Dockerfile`(ffmpeg 포함)이고, compose 서비스는 worker-gpu(`q.transcode`·`q.deident`)입니다.

## 실행

```bash
uv run --package worker-jobs celery -A worker_jobs.celery_app worker -l info \
  -Q q.transcode -c 2 --prefetch-multiplier 1 -n transcode@%h
uv run --package worker-jobs --group test pytest tests/     # DB·브로커 없이 실행합니다
```

## 남은 일

- 없음.
