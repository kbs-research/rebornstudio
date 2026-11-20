# worker-jobs/tests

## 목적

DB를 쓰지 않는 작업(잡) 태스크의 등록·회신 계약 테스트입니다. 브로커와 DB 없이 실행합니다. 러너는 흉내 내고, 회신 메시지를 가로채 내용을 확인합니다.

## 주요 파일

| 파일 | 확인하는 것 |
|---|---|
| `test_jobs.py` | 앱이 `JOB_TASKS` 네 개만 등록하는지, 잡 태스크의 재전달 옵션(`acks_late`·`reject_on_worker_lost`·`ignore_result`), 라우팅이 계약 표와 같은지, 예상하지 못한 예외에도 완료 보고를 보내는지, 성공 보고에 산출 측정값이 실리는지, 일하기 전에 시작 하트비트를 보내는지 |

## 설계 이유

- 이름이 어긋나면 worker-io가 보낸 메시지가 이 워커에서 등록되지 않은 태스크가 되어 버려집니다. 등록 이름을 `reborn_contracts.queues`와 대조해 이 실패를 막습니다.
- DB를 쓰는 태스크가 이 앱에 등록되면 잡 이미지에 DB 설정이 필요해집니다. 그래서 정확히 네 개만 등록되는지 확인합니다.

## 의존성

- `pytest`(dependency group `test`)
- 실행: `(cd workers/worker-jobs && uv run --package worker-jobs --group test pytest tests/)`. CI는 `api-gateway` 잡에서 실행합니다.

## 남은 일

- 없음.
