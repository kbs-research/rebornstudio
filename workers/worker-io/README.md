# worker-io

## 목적

DB를 쓰는 Celery 워커 패키지입니다. beat 스케줄로 주기 작업(MI 분석 결과 회수, 파이프라인 리스 회수, 내보내기, 썸네일, probe 등)을 실행합니다.
DB를 쓰지 않는 작업(이하 잡)을 이름으로 투입하고, 그 회신을 DB에 반영하고, 캡션 품질검수를 실행합니다.
태스크는 `api_gateway` 서비스 함수를 부르는 얇은 껍데기이고, 판정 로직은 서비스에 있습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `pyproject.toml` | 패키지 정의. 의존성은 `celery[redis]`·`api-gateway`·`reborn-contracts` |
| [`worker_io/`](worker_io/README.md) | Celery 앱(`celery_app.py`)과 태스크 패키지(`tasks/`) |
| [`tests/`](tests/README.md) | 태스크 등록·라우팅·beat 스케줄, eager 모드 실행 테스트 |

## 설계 이유

모든 태스크가 지키는 신뢰성 계약은 아래와 같습니다.

1. beat 주기 태스크는 대상을 DB에서 골라 처리하는 멱등 작업입니다. 메시지가 유실되어도 다음 주기가 같은 대상을 다시 고릅니다.
   그래서 수신 즉시 ack하는 기본 설정으로도 안전합니다.
2. 특정 id를 받는 대상 지정 태스크는 재전달과 중복 실행을 전제로 합니다. `acks_late`와 `reject_on_worker_lost`로 메시지를 다시 전달받습니다.
   DB 리스로 점유자를 가리고, 펜싱 토큰(현재 시도가 아닌 보고를 버리는 토큰)으로 낡은 시도의 결과를 걸러냅니다. 리스가 만료되면 회수 주기 태스크가 대상을 되돌립니다.
3. 태스크 결과는 저장하지 않습니다(`task_ignore_result=True`). 결과가 필요한 태스크가 생기면 그 태스크에만 켭니다.
   30초 이하 주기 태스크의 메시지는 주기가 지나면 만료됩니다(`expires`). 워커가 멈춘 동안 쌓인 메시지가 돌아온 뒤 같은 일을 되풀이하지 않습니다.
4. 긴 태스크의 종료 유예는 compose의 `stop_grace_period`가 정합니다. 진행 중인 한 건을 끝내거나 리스를 반납할 시간입니다.
5. 태스크 수신·성공 줄(`celery.worker.strategy`·`celery.app.trace`)과 beat 발송 줄(`celery.beat`)은 WARNING 이상만 남깁니다(`_quiet_polling_http_logs`). 짧은 주기 태스크가 여럿이라 이 줄만으로 분당 백 줄을 넘고, 로그 회전 상한 안에서 며칠치만 남기 때문입니다. 태스크 실패는 ERROR로 남습니다.
6. 브로커 재전달 시간(`broker_transport_options.visibility_timeout`)은 worker-jobs 앱과 같은 값(`reborn_contracts.queues.BROKER_VISIBILITY_TIMEOUT_S`, 24시간)입니다.
   확인 응답이 없는 메시지는 같은 Redis에 붙은 어느 워커든 자기 설정값으로 되돌리므로, 이 앱이 기본값(1시간)이면 긴 잡이 실행 중에 다시 전달됩니다.

새 태스크를 더할 때 확인할 것은 다음과 같습니다.

- 메시지가 유실되면 다음 주기가 다시 고르는가, 아니면 리스와 펜싱 토큰이 있는가
- 두 번 실행되어도 결과가 같은가(중복 방지 키, 조건부 UPDATE)
- 처리 시간이 비슷한 큐에 넣었는가. 수십 분짜리 작업을 초 단위 큐(`q.media`)에 넣지 않습니다
- 태스크 본문이 서비스 함수 호출 한 줄인가
- `reborn_contracts.queues`와 [`packages/reborn-contracts/queues.md`](../../packages/reborn-contracts/queues.md)에 적었는가

## 의존성

- 이 패키지가 부르는 것: `api_gateway.services.*`, `api_gateway.config`, `reborn_contracts.queues`, Redis(`REDIS_URL`), Postgres(`DATABASE_URL`)
- 부르는 쪽: compose 서비스 worker(beat 포함)·worker-qc
- 잡은 [`../worker-jobs`](../worker-jobs/README.md)가 실행합니다. 이 패키지는 잡을 import하지 않고 이름으로 보냅니다.

## 실행

```bash
# 로컬 단일 워커(-B: beat를 같은 프로세스에서 실행). q.transcode·q.deident는 worker-jobs가 소비합니다
uv run --package worker-io celery -A worker_io.celery_app worker -B -l info -Q default,q.maiu,q.media,q.qc

# 테스트. CELERY_TASK_ALWAYS_EAGER=1로 태스크를 inline 실행합니다
uv run --package worker-io --group test pytest tests/
```

테스트는 `DATABASE_URL`의 DB(마이그레이션 적용 필요)와 `REDIS_URL`에 접속합니다. 기본값은 로컬 개발 스택의 DB입니다. 개발 데이터와 섞이지 않게 하려면 테스트용 DB를 `DATABASE_URL`로 줍니다.
CI는 `api-gateway` 잡에서 서버 테스트 뒤에 이 테스트를 실행합니다.

## 남은 일

- 없음.
