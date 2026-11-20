# tasks

## 목적

worker-io의 Celery 태스크 패키지입니다. 태스크는 `api_gateway` 서비스 함수를 부르는 얇은 껍데기입니다.
DB를 쓰지 않는 작업(이하 잡)은 `workers/worker-jobs`가 실행합니다. 이 패키지는 잡을 이름으로 보내고 회신을 DB에 반영합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `__init__.py` | 하위 모듈을 모두 import해 태스크를 등록하고, `worker_io.tasks.<이름>` 경로로 다시 내보냅니다 |
| `sweeps.py` | 대상을 DB에서 골라 처리하는 주기 태스크. 분석 결과 회수·연동과 연결 점검·원본 정리·체크섬·내보내기·감사 이벤트 정리·폴더 등록·썸네일·probe. `check_connections`는 연결과 함께 프로젝트별 저장소·분석 대상도 점검합니다(`project_stack.recheck_all`) |
| `dispatch.py` | 잡을 이름으로 큐에 넣는 `dispatch_*` 4종과 리스 만료를 되돌리는 `reclaim_expired_*` 4종 |
| `callbacks.py` | 잡이 보낸 하트비트·완료 보고를 DB에 반영하는 8종 |
| `qc.py` | 캡션 품질검수. `dispatch_caption_qc`(5초 주기, `default`)가 검사를 받지 못한 제출·승인을 찾아 `caption_qc_run`(`q.qc`)으로 보냅니다 |
| `_session.py` | 태스크마다 새 엔진으로 세션을 여는 `_run_with_fresh_session`. 본문을 실행하기 전에 등록된 저장소·분석 연결을 다시 읽습니다(`platform_credentials.refresh_if_stale`) |

## 설계 이유

- 잡 하나는 아래 순서로 처리됩니다. DB를 만지는 단계는 투입, 회신, 회수뿐입니다.
  판정은 회신을 받는 서비스 함수 한 곳에서 합니다.

  ```
  dispatch(투입) → worker-jobs의 jobs(실행) → callbacks(하트비트·완료) → dispatch(리스 만료 회수)
  ```

- 잡은 이름으로 보냅니다. `dispatch`는 잡 함수를 import하지 않고, `reborn_contracts.queues`의 이름으로 `send_task`를 호출합니다.
  `send_task`는 태스크 옵션을 모르므로 `_send_job`이 `ignore_result=True`와 직렬화 방식을 명시합니다. 빠지면 결과 백엔드가 잡마다 채널을 구독합니다.
- 잡 메시지의 id는 펜싱 토큰(현재 시도가 아닌 보고를 버리는 토큰)입니다. `_send_job`이 토큰을 `task_id`로 싣고, `reclaim_expired_*`는 회수한 옛 토큰을 `_revoke_messages`로 취소 방송합니다.
  워커는 큐에서 기다리던 옛 메시지를 시작 전에 버립니다. 그래서 다시 투입된 시도와 함께 같은 영상을 두 번 처리하지 않습니다. 취소를 놓쳐도 옛 시도의 완료 보고는 토큰 확인에서 버려집니다.
- 품질검수는 요청 경로 밖에서 실행합니다. api는 브로커에 메시지를 보내지 않고 커밋만 하며, 주기 태스크가 대상을 DB에서 다시 찾습니다.
  그래서 api 재시작, 메시지 유실, 워커 중단이 모두 다음 주기에 회수됩니다. 대상 판정과 중복 방지는 `caption_qc.backlog`·`runner.run_owed`가 합니다.
- 모든 태스크가 `name="worker_io.tasks.<함수명>"`을 명시합니다. 라우팅 표, beat 스케줄, 하트비트 Redis 키, 큐에 남은 메시지가 이 이름을 가리키므로 파일을 옮겨도 이름은 그대로 둡니다.
- `celery_app`의 `include=["worker_io.tasks"]`는 이 패키지만 import합니다. 새 하위 모듈은 `__init__.py`에서 import해야 등록됩니다.
  빠뜨리면 워커가 그 이름의 메시지를 등록되지 않은 태스크로 버립니다.

## 새 태스크를 둘 곳

- DB에서 대상을 골라 처리하는 주기 작업: `sweeps.py`
- 새 종류의 잡: 잡 본체는 `workers/worker-jobs/worker_jobs/jobs.py`, 투입·회수는 `dispatch.py`, 회신은 `callbacks.py`
- 요청 뒤에 오래 걸리는 DB 작업: 요청은 커밋만 하고, 대상을 다시 찾는 주기 태스크와 한 건 태스크로 나눈다(`qc.py`)
- 이름·큐는 `reborn_contracts.queues`에, 주기는 `../celery_app.py`에, 페이로드는 `packages/reborn-contracts/queues.md`에 같이 적습니다.
  확인 목록은 [`workers/worker-io/README.md`](../../README.md)에 있습니다.

## 의존성

- `..celery_app.app`: 모든 모듈
- `api_gateway.config`·`api_gateway.services.*`: `sweeps`·`dispatch`·`callbacks`·`qc`·`_session`(서비스는 함수 안에서 import합니다)
- `reborn_contracts.queues`: `dispatch`(잡 이름·큐)·`callbacks`(회신 이름)·`qc`

## 남은 일

- 없음.
