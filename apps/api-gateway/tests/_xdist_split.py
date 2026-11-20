"""xdist 병렬 실행에서 이 워커 전용 DB와 Redis 인덱스로 바꾼다.

`settings`가 import 시점에 env를 읽고 캐싱하므로 `conftest`가 앱을 import하기 전에 이 모듈을 import한다.
부작용이 목적인 모듈이라 함수를 부르지 않고 import만 한다.

테스트 격리는 트랜잭션이 아니라 고유 email·slug로 한다.
같은 DB에 워커 여럿이 붙으면 `conftest.pytest_sessionfinish`의 정리 DELETE가
다른 워커가 쓰는 중인 행까지 지운다.
Redis도 같다. `test_integration_watch`가 지우는 `_SNAPSHOT_KEY`는 고정 키라서
워커 둘이 같은 인덱스를 쓰면 서로의 상태를 지운다.

워커 DB는 템플릿 복사로 만든다. 워커마다 `alembic upgrade head`를 돌리면 비용이 워커 수만큼 늘어난다.
`CREATE DATABASE … TEMPLATE`는 파일 복사라 비용이 훨씬 적다.
템플릿 복사는 템플릿 DB에 다른 연결이 없어야 한다. 워커는 자기 DB에만 연결하므로 이 조건을 지킨다.
"""
import os
import re
import time

import psycopg

_WORKER = os.environ["PYTEST_XDIST_WORKER"]          # gw0, gw1, …
_N = int(re.sub(r"\D", "", _WORKER) or 0)

# Redis 인덱스는 기본 16개(0~15)뿐이다. 넘으면 인덱스를 되돌려 써서 워커끼리 겹치므로 여기서 막는다.
# 0번은 단독 실행용으로 비워 둔다.
if _N >= 15:
    raise RuntimeError(f"워커가 너무 많다({_WORKER}) — Redis 인덱스가 겹친다. -n 15 이하로 돌리세요.")
os.environ["REDIS_URL"] = re.sub(r"/\d+$", f"/{_N + 1}", os.environ["REDIS_URL"])

_url = os.environ["DATABASE_URL"]
_template = _url.rsplit("/", 1)[1]
_mine = f"{_template}_{_WORKER}"
_admin_dsn = _url.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0] + "/postgres"

# 워커 DB는 매번 새로 만든다. 지난 실행의 잔여 행이 남으면 unique 제약이 엉뚱한 테스트에서 실패한다.
# 이 실패는 코드 결함으로 오인하기 쉽다. 템플릿 복사라 비용이 거의 없다.
#
# 워커들이 같은 템플릿에서 동시에 만들면 Postgres가 잠깐 거절한다("being accessed by other users").
# 원인은 동시에 실행한 CREATE끼리의 충돌이라 곧 풀린다. 그래서 짧게 재시도한다.
for _attempt in range(12):
    try:
        with psycopg.connect(_admin_dsn, autocommit=True) as _conn, _conn.cursor() as _cur:
            _cur.execute(f'DROP DATABASE IF EXISTS "{_mine}"')
            _cur.execute(f'CREATE DATABASE "{_mine}" TEMPLATE "{_template}"')
        break
    except psycopg.errors.ObjectInUse:
        time.sleep(0.5 * (_attempt + 1))
else:
    raise RuntimeError(f"{_mine} 생성 실패 — 템플릿 {_template}에 다른 연결이 남아 있습니다")

os.environ["DATABASE_URL"] = _url.rsplit("/", 1)[0] + "/" + _mine
