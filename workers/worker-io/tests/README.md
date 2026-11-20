# worker-io/tests

## 목적

worker-io 앱의 태스크 등록, 라우팅, beat 스케줄을 확인합니다. 일부 태스크는 eager 모드(`CELERY_TASK_ALWAYS_EAGER=1`)로 실제 DB에 대고 실행합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `conftest.py` | 테스트 환경. 로컬 `.env`를 읽지 않게 `REBORN_ENV_FILE`을 비우고, `DATABASE_URL`·`REDIS_URL` 기본값과 eager 모드를 정합니다 |
| `test_celery_tasks.py` | 앱과 beat 스케줄 등록, 분석 결과 회수·점유 회수 태스크의 eager 실행, 씬 분할·품질검수 태스크의 라우팅과 주기, 잡은 이름으로만 보내고 이 앱에 등록하지 않는지, 라우팅 표의 이름마다 등록된 앱이 정확히 하나인지, 회수 태스크가 옛 메시지를 취소하는지, fork된 자식이 물려받은 커넥션 풀을 닫지 않고 버리는지 |

## 설계 이유

- eager 모드로 태스크를 같은 프로세스에서 실행해, 워커 프로세스 없이 서비스 함수까지 검증합니다.
- 라우팅 표의 모든 이름이 worker-io와 worker-jobs 중 정확히 한 앱에 등록되는지 확인합니다. 두 앱에 모두 있으면 옛 코드가 다른 이미지에서 실행될 수 있고, 어디에도 없으면 메시지가 버려집니다.

## 의존성

- Postgres(`DATABASE_URL`, 마이그레이션 적용 필요)와 Redis(`REDIS_URL`). 기본값은 로컬 개발 스택의 DB(`localhost:5433/rebornstudio`)입니다. 개발 데이터와 섞이지 않게 하려면 테스트용 DB를 줍니다.
- `pytest`·`pytest-asyncio`(dependency group `test`)
- 실행: `(cd workers/worker-io && uv run --package worker-io --group test pytest tests/)`. CI는 `api-gateway` 잡에서 서버 테스트 뒤에 실행합니다.

## 남은 일

- 없음.
