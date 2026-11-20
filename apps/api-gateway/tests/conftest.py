"""테스트 공통 fixture와 DB·Redis·외부 연동 설정.

기본 DB는 `postgresql+asyncpg://reborn:reborn@localhost:5433/rebornstudio_test`,
기본 Redis는 `redis://localhost:6380/0`이다. 다른 곳을 쓰려면 `DATABASE_URL`·`REDIS_URL`을 넘긴다.
테스트 DB에는 먼저 `alembic upgrade head`를 적용한다.
테스트 격리는 트랜잭션 rollback 대신 테스트마다 고유 email·slug를 쓰는 방식으로 한다.
앱이 쓰는 DB에서 돌리면 테스트 프로젝트가 쌓여 앱 목록에 보이므로 테스트 전용 DB를 쓴다.

CI와 `make verify-all`은 병렬(`-n 4`)로 돌린다. 이때 워커마다 자기 DB와 자기 Redis 인덱스를 쓴다.
이 분리는 `tests/_xdist_split.py`가 하고, 필요한 이유는 그 모듈 docstring에 있다.
워커 DB 이름은 `<기본DB>_gw0` 형식이고 매 실행마다 템플릿에서 다시 만든다.
그래서 DB가 누적되지 않고 워커 수만큼만 남는다.
단독으로 돌리려면 `-n 0`을 주거나 옵션을 뺀다. 이때는 기본 DB 하나를 쓴다.

주의: 같은 Postgres·Redis에 로컬 개발 스택의 api와 워커가 붙어 있으면 전체 실행이 불안정해진다.
DB는 워커별로 나눠도 Postgres 인스턴스와 Redis는 같은 컨테이너를 쓴다.
그래서 커넥션 경합과 asyncpg 루프 결합으로 실행마다 다른 테스트가 실패하고 실행 시간도 늘어난다.
전체 실행 전에 개발 스택의 api와 워커를 멈추거나 전용 Postgres·Redis를 쓴다.

    docker compose -f infra/compose/compose.yaml -f infra/compose/compose.dev.yaml \\
        stop worker worker-qc worker-gpu api web
    uv run --package api-gateway --group test pytest tests/ -n 4
"""
import os
import secrets

# live 테스트가 아니면 로컬 `.env`를 읽지 않는다.
# config는 실행 위치와 무관하게 저장소 루트의 `.env`를 찾는다.
# 막지 않으면 로컬 비밀값과 토글이 테스트에 섞여 CI와 결과가 달라진다.
# live 테스트는 루트 `.env`의 DB에 등록한 MI 연결을 읽으므로 그 파일을 그대로 읽는다.
if os.getenv("MAIU_LIVE_TEST") != "1":
    os.environ.setdefault("REBORN_ENV_FILE", "")

# settings가 캐싱되기 전에 적용되도록 DB URL을 conftest import 시점에 설정한다.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://reborn:reborn@localhost:5433/rebornstudio_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6380/0")

# xdist 병렬 실행이면 이 워커 전용 DB·Redis로 바꾼다. 부작용을 위한 import다.
# 워커가 같은 DB를 보면 정리 DELETE가 다른 워커의 행을 지운다.
# Redis 고정 키(`_SNAPSHOT_KEY`)도 서로 덮어쓴다. 자세한 이유는 그 모듈 docstring에 있다.
if os.getenv("PYTEST_XDIST_WORKER"):
    import tests._xdist_split  # noqa: F401

# 오브젝트 스토리지는 인프로세스 moto가 대신한다. 테스트는 외부 NCP를 호출하지 않는다.
# 앱은 저장소 자격증명을 환경 변수가 아니라 등록한 연결에서 읽는다. moto를 가리키는 연결은
# 세션 픽스처(`_storage_connection`)가 DB에 등록한다(`tests/_storage.py`).
import tests._moto_bootstrap  # noqa: F401

# 연결 주소 검사는 루프백을 막는다. 테스트 저장소 연결은 루프백의 moto를 가리키므로 그 호스트만 연다.
# 주소 검사 자체를 보는 테스트는 허용 목록을 비우고 돈다(`test_connection_address_guard`).
os.environ.setdefault("CONNECTION_HOST_ALLOWLIST", "127.0.0.1")

# live 테스트가 아니면 MAIU를 끈다. MAIU는 외부 API라서 moto 같은 인프로세스 대역이 없다.
# `REBORN_ENV_FILE`이 가리키는 `.env`에 `MAIU_ENABLED=true`가 있으면 테스트가 실제 MI를 호출한다.
# 객체는 moto 안에만 있으므로 MI 호출은 `10041 버킷 연동이 유효하지 않습니다`로 실패한다.
# 이 오류는 NCP 연동 장애처럼 보이지만 원인은 테스트 설정이다.
# `test_setup.py`는 테스트 기본값이 `MAIU_ENABLED=false`(가짜 클라이언트)라고 전제한다.
# 실호출은 `@pytest.mark.live`와 `MAIU_LIVE_TEST=1`로만 연다.
if os.getenv("MAIU_LIVE_TEST") != "1":
    os.environ["MAIU_ENABLED"] = "false"

# config의 운영 비밀값 검증을 건너뛰도록 test 환경을 명시한다.
os.environ.setdefault("ENVIRONMENT", "test")

import pytest
import pytest_asyncio
from api_gateway.main import app
from httpx import ASGITransport, AsyncClient


def pytest_collection_modifyitems(config, items):
    """`live` 마커가 붙은 테스트를 `MAIU_LIVE_TEST=1`일 때만 실행한다. 외부 API 호출을 막는다."""
    if os.getenv("MAIU_LIVE_TEST") == "1":
        return
    skip_live = pytest.mark.skip(reason="MAIU_LIVE_TEST=1로 활성화")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


def pytest_sessionfinish(session, exitstatus):
    """세션이 끝날 때 누적된 테스트 잔여 행을 지운다.

    고유 email·slug를 쓴 행은 실행마다 DB에 쌓인다. 슬러그 패턴과 이메일 패턴으로 찾아 한꺼번에 지운다. 조직은 기본 조직 한 행뿐이라 지우지 않는다.
    `PYTEST_NO_CLEANUP=1`이면 건너뛴다. 잔여 행을 디버깅할 때 쓴다.
    정리가 실패해도 테스트 결과(exitstatus)는 바꾸지 않는다.
    """
    if os.getenv("PYTEST_NO_CLEANUP") == "1":
        return
    # 병렬 워커의 DB는 시작할 때 템플릿에서 새로 만드는 일회용이라 잔여가 쌓이지 않는다.
    # 여기서 지우면 워커 수만큼 같은 DELETE가 돌 뿐이다.
    if os.getenv("PYTEST_XDIST_WORKER"):
        return
    import sys

    import psycopg
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            # `uniq()` 슬러그는 'prefix-<8hex>'(대시 하나) 형식이라 끝이 `-<8hex>`인 slug를 지운다.
            # 이름이 'admin-ok'인 테스트 프로젝트도 함께 지운다.
            cur.execute(
                "DELETE FROM projects WHERE slug ~ '-[0-9a-f]{8}$' OR name = 'admin-ok'"
            )
            cur.execute(
                "DELETE FROM domain_events WHERE actor_user_id IN "
                "(SELECT user_id FROM users WHERE email ~ '-[0-9a-f]{8}@example\\.com$')"
            )
            cur.execute("DELETE FROM users WHERE email ~ '-[0-9a-f]{8}@example\\.com$'")
    except Exception as e:  # noqa: BLE001
        print(f"\n[conftest cleanup] non-fatal: {e}", file=sys.stderr)


@pytest_asyncio.fixture(loop_scope="session", scope="session", autouse=True)
async def _storage_connection():
    """moto를 가리키는 NCP 저장소 연결을 세션에 한 번 등록한다. 테스트 프로젝트가 이 연결을 고른다."""
    from tests import _storage
    return await _storage.ensure()


#: 연결 점검이 테스트 프로세스 안에서 끝나는 주소의 호스트. moto와 일부러 닫힌 포트가 루프백에 있다.
_IN_PROCESS_HOSTS = frozenset({"127.0.0.1", "localhost"})


def _probe_stays_in_process(conn) -> bool:
    """연결 점검이 테스트 밖 서비스에 접속하지 않으면 참이다.

    MI 점검은 연동이 꺼진 테스트 설정에서 접속하지 않는다. 연동을 켜는 테스트는 클라이언트를 대역으로 바꾼다.
    GCS 점검은 접속하지 않는다. 저장소와 모델 연결은 주소가 루프백일 때만 프로세스 안에서 끝난다.
    """
    from urllib.parse import urlsplit

    if conn.provider in ("maiu", "gcs"):
        return True
    cfg = conn.config or {}
    url = cfg.get("endpoint") or cfg.get("base_url") or ""
    return (urlsplit(str(url)).hostname or "") in _IN_PROCESS_HOSTS


@pytest.fixture(autouse=True)
def _no_external_connection_probe(request, monkeypatch):
    """연결 점검이 외부 클라우드·모델 서비스에 접속하지 않게 한다.

    연결을 저장하면 같은 요청에서 점검이 돈다(`connections.check_saved`). 테스트는 가짜 주소와 키로
    연결을 많이 만든다. 그대로 두면 저장마다 외부에 접속해 느려지고, 네트워크가 없으면 결과가 바뀐다.
    프로세스 밖으로 나가는 점검은 접속 없이 실패로 끝낸다. 점검 분기 자체를 보는 테스트는
    `real_probe` 표지로 이 대역을 끈다.
    """
    if request.node.get_closest_marker("real_probe"):
        return
    from api_gateway.services.stacks import connections as conn_service
    from reborn_contracts.connection import ConnectionTestResult

    real = conn_service._probe

    async def _guarded(conn, secret):
        """프로세스 안에서 끝나는 점검만 실제 분기로 보낸다."""
        if _probe_stays_in_process(conn):
            return await real(conn, secret)
        return ConnectionTestResult(status="fail", detail="시험에서는 외부 서비스에 접속하지 않습니다")

    monkeypatch.setattr(conn_service, "_probe", _guarded)


# HTTP 클라이언트는 세션에 한 번만 만든다.
# `loop_scope`는 픽스처가 도는 이벤트 루프를 정하고, `scope`는 다시 만드는 주기를 정한다.
# 둘은 다른 설정이라서 둘 다 session으로 준다. `loop_scope`만 주면 테스트마다 새로 만든다.
# 테스트끼리 클라이언트 하나를 같이 써도 안전하다. 테스트는 헤더를 클라이언트에 넣지 않고 요청마다 넘긴다.
@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def client():
    """앱을 ASGI로 직접 구동하는 세션 공용 HTTP 클라이언트를 만든다.

    앱이 주는 refresh 쿠키는 `Path=/api/auth`라서 `/api` 접두 없이 부르는 테스트 요청에 붙지 않는다.
    테스트마다 만들면 `admin_token`이 매번 시드와 로그인을 돌고, 의도적으로 비싼 비밀번호 해시가
    테스트당 약 0.4초씩 더해진다.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# super_admin 시드 비밀번호. 세션마다 무작위 값을 만들어 시드와 로그인에 같이 쓴다.
# `seed_admin`이 매번 비밀번호를 이 값으로 맞추므로 DB에 남은 이전 비밀번호와 무관하게 로그인된다.
# 저장소에 평문 비밀번호를 두지 않는다. `ADMIN_TEST_PASSWORD`가 있으면 그 값을 쓴다.
_ADMIN_PASSWORD = os.getenv("ADMIN_TEST_PASSWORD") or secrets.token_urlsafe(18)


# admin 토큰은 세션에 한 번만 만든다(위 `client` 주석).
# 주의: admin 상태가 망가져도 다음 테스트가 되살리지 않는다.
# admin을 바꾸는 테스트는 스스로 복구해야 한다. `test_setup`의 정지·복구는 `finally`로 되돌린다.
@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def admin_token(client: AsyncClient) -> str:
    """super_admin을 시드하고 로그인해 세션 공용 access 토큰을 만든다."""
    # 마이그레이션 0057이 0002의 시드 admin을 정지시키므로 테스트가 직접 시드해 다시 활성화한다.
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "dev"))
    from seed_admin import seed_admin
    await seed_admin(password=_ADMIN_PASSWORD)

    r = await client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": _ADMIN_PASSWORD},
    )
    if r.status_code == 200:
        return r.json()["session"]["access_token"]
    raise RuntimeError(f"admin@example.com 로그인 실패({r.status_code}): 시드가 비밀번호를 맞추지 못했다.")


def uniq(prefix: str) -> str:
    """테스트끼리 겹치지 않는 고유 email을 만든다.

    도메인은 RFC 2606 예약 도메인인 example.com이다.
    """
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture()
def fake_ffmpeg(monkeypatch):
    """`transcode_streaming`만 가짜로 바꾼다. 잡 러너의 오케스트레이션(스레드·PUT)은 실제 코드가 돈다.

    transcode와 publish가 같은 러너를 쓰므로 픽스처도 하나다.
    """
    import reborn_transcode.job_runner as jr

    calls: list = []

    def _fake(input_uri, output_path, spec, *, on_progress=None, cancel=None, ffmpeg="ffmpeg"):
        """변환 대신 호출을 기록하고 빈 출력 파일을 쓴다."""
        calls.append({"input": input_uri, "spec": spec})
        if on_progress:
            on_progress(1.0)
        output_path.write_bytes(b"\x00" * 256)

    monkeypatch.setattr(jr, "transcode_streaming", _fake)
    return calls
