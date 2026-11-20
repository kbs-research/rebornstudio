# worker_jobs

## 목적

worker-jobs의 파이썬 패키지입니다. Celery 앱과, DB를 쓰지 않는 작업(잡)의 태스크 4종이 있습니다. beat 스케줄은 없습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `__init__.py` | 패키지 표식. 아무것도 다시 내보내지 않습니다 |
| `celery_app.py` | Celery 앱(`include=["worker_jobs.jobs"]`). 브로커·직렬화·결과 설정은 worker-io 앱과 같고, 라우팅 표는 `reborn_contracts.queues`를 읽습니다. httpx의 요청별 INFO 로그를 끕니다. 기동할 때와 30초마다 `reborn:node:gpu`에 동작 시각을 씁니다(주기 태스크가 없어 연동 진단이 이 기록으로 멈춤을 봅니다) |
| `jobs.py` | 잡 태스크 `transcode_one`·`scene_split_one`·`publish_one`·`deident_one`과 예상하지 못한 예외의 사유 `UNEXPECTED_ERROR`. `TRANSCODE_THREADS`가 있으면 인코딩 스레드 수를 그 값으로 덮어씁니다 |

## 설계 이유

- 태스크 이름과 회신 이름은 `reborn_contracts.queues`의 상수만 씁니다. 문자열을 직접 쓰면 worker-io와 이름이 어긋나도 빌드가 통과합니다.
- httpx INFO 로그는 끕니다. httpx는 요청 주소 전체를 찍는데, 잡의 입력·산출 주소는 서명이 든 presigned URL입니다.

## 의존성

- 이 폴더가 부르는 것: `reborn_transcode`(`run_job`·`scene_split.run_job`·`run_deident_job`), `reborn_contracts.queues`, `celery`
- 부르는 쪽: `celery -A worker_jobs.celery_app`(compose 서비스 worker-gpu), `../tests`

## 새 잡을 둘 곳

잡 본체는 `jobs.py`에, 이름·큐는 `reborn_contracts.queues`에, 투입·회수·회신은 worker-io의 `tasks/dispatch.py`·`tasks/callbacks.py`에 둡니다.
페이로드 스키마는 [`packages/reborn-contracts/queues.md`](../../../packages/reborn-contracts/queues.md)에 적습니다.

## 남은 일

- 없음.
