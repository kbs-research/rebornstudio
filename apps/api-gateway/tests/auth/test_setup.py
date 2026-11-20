"""최초 설정 흐름 테스트.

세 가지를 검증한다.
  1. 배포 직후에는 로그인할 수 있는 계정이 없다.
     저장소에 비밀번호가 알려진 계정이 활성 상태로 남아 있으면 안 된다.
  2. 첫 관리자는 서버 로그를 읽을 수 있는 사람만 만든다(셋업 토큰).
  3. 동시에 두 번 눌러도 관리자는 한 명만 생긴다.
"""
import asyncio

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import User
from api_gateway.services.auth import setup as setup_service
from httpx import AsyncClient
from sqlalchemy import select, update
from tests._helpers import uniq


@pytest.fixture(autouse=True)
def _seeded_admin(admin_token: str) -> None:
    """시드 관리자 계정을 먼저 만든다.

    테스트마다 모든 관리자를 정지한 뒤 `_restore`가 시드 계정을 되살려 초기화된 상태로 돌린다.
    마이그레이션은 시드 계정을 만들지 않으므로 세션 픽스처가 만들기 전에 이 파일이 먼저 돌면
    되살릴 계정이 없어 다음 테스트가 초기화 전 상태에서 시작한다.
    """


async def _suspend_all_admins() -> None:
    """운영 배포 직후 상태를 만든다. 마이그레이션 0057이 시드 계정을 정지시킨 상태다."""
    async with SessionLocal() as db:
        await db.execute(
            update(User).where(User.global_role == "super_admin")
            .values(approval_status="suspended")
        )
        await db.commit()


async def _restore(email: str) -> None:
    """계정을 다시 active로 되돌린다."""
    async with SessionLocal() as db:
        await db.execute(
            update(User).where(User.email == email).values(approval_status="active"))
        await db.commit()


async def test_status_says_uninitialized_when_no_active_admin(client: AsyncClient):
    """정지된 시드 계정을 활성 관리자로 세지 않아 미초기화로 판정하는지 확인한다. 0057이 시드 계정을 지우지 않고 정지시키는 근거다."""
    await _suspend_all_admins()
    try:
        r = await client.get("/setup/status")
        assert r.status_code == 200
        assert r.json()["uninitialized"] is True
        # 초기화 전 서버에는 누구나 요청할 수 있으므로 상태 말고는 아무것도 싣지 않는다.
        assert set(r.json()) == {"uninitialized", "token_required"}
    finally:
        await _restore("admin@example.com")


async def test_complete_creates_admin_then_closes_the_door(client: AsyncClient):
    """첫 관리자를 만든 뒤에는 최초 설정 경로가 다시 열리지 않는지 확인한다."""
    await _suspend_all_admins()
    email = uniq("owner")
    try:
        token = setup_service.issue_token() or ""
        r = await client.post("/setup/complete", json={"privacy_consent": True,
            "email": email, "display_name": "첫 관리자",
            "password": "SetupPass!2026x", "token": token,
        })
        assert r.status_code == 201, r.text

        # 만든 계정으로 실제 로그인이 되어야 설정이 끝난다. 계정 행만으로는 로그인을 보장하지 않는다.
        login = await client.post("/auth/login", json={
            "email": email, "password": "SetupPass!2026x"})
        assert login.status_code == 200, login.text

        # 이제 초기화됐으므로 두 번째 요청은 거부된다.
        assert (await client.get("/setup/status")).json()["uninitialized"] is False
        again = await client.post("/setup/complete", json={"privacy_consent": True,
            "email": uniq("second"), "display_name": "두 번째",
            "password": "SetupPass!2026x", "token": token,
        })
        assert again.status_code == 409
    finally:
        await _restore("admin@example.com")


async def test_wrong_token_is_refused(client: AsyncClient):
    """틀린 토큰으로는 관리자를 만들 수 없는지 확인한다. 서버 로그를 읽을 수 있어야 관리자가 된다."""
    await _suspend_all_admins()
    try:
        setup_service.issue_token()
        r = await client.post("/setup/complete", json={"privacy_consent": True,
            "email": uniq("intruder"), "display_name": "침입자",
            "password": "SetupPass!2026x", "token": "wrong-token",
        })
        assert r.status_code == 403
        async with SessionLocal() as db:
            assert await db.scalar(
                select(User).where(User.display_name == "침입자")) is None
    finally:
        await _restore("admin@example.com")


async def test_first_admin_password_follows_the_policy(client: AsyncClient):
    """첫 관리자도 같은 비밀번호 정책을 따르는지 확인한다.

    변경·재설정과 달리 최초 설정에서 정책을 빠뜨리면 `aaaaaaaaaaaa`(12자 소문자)로도 가장 권한이 큰 계정이 생긴다.
    """
    await _suspend_all_admins()
    email = uniq("weak-owner")
    try:
        token = setup_service.issue_token() or ""
        r = await client.post("/setup/complete", json={"privacy_consent": True,
            "email": email, "display_name": "약한 관리자",
            "password": "aaaaaaaaaaaa", "token": token,
        })
        assert r.status_code == 422, r.text
        assert "12자 이상" in r.json()["detail"], "정책 문장은 변경·재설정과 같다"
        async with SessionLocal() as db:
            assert await db.scalar(select(User).where(User.email == email)) is None
    finally:
        await _restore("admin@example.com")


async def test_first_admin_requires_privacy_consent(client: AsyncClient):
    """첫 최고관리자도 개인정보 수집·이용 동의가 있어야 만들어지는지 확인한다.

    동의가 없으면 422 `privacy_consent_required`이고 계정이 생기지 않는다.
    동의하면 동의 시각이 기록되고 `user.privacy_consented` 이벤트가 `via=setup`으로 남는다.
    """
    from api_gateway.models import DomainEvent

    await _suspend_all_admins()
    email = uniq("consent-owner")
    try:
        token = setup_service.issue_token() or ""
        body = {"email": email, "display_name": "동의 관리자", "password": "SetupPass!2026x",
                "token": token}
        r = await client.post("/setup/complete", json=body)
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == "privacy_consent_required"
        async with SessionLocal() as db:
            assert await db.scalar(select(User).where(User.email == email)) is None

        r = await client.post("/setup/complete", json={**body, "privacy_consent": True})
        assert r.status_code == 201, r.text
        async with SessionLocal() as db:
            user = await db.scalar(select(User).where(User.email == email))
            assert user.privacy_consented_at is not None
            via = await db.scalar(select(DomainEvent.payload["via"].astext).where(
                DomainEvent.event_type == "user.privacy_consented",
                DomainEvent.target_id == user.user_id,
            ))
            assert via == "setup"
    finally:
        await _restore("admin@example.com")


class _BrokenRedis:
    """응답하지 않는 Redis 토큰 저장소. 조회가 예외를 던진다."""

    def get(self, _key):
        """Redis 연결 오류를 낸다."""
        raise ConnectionError("redis down")

    def set(self, *_a, **_k):
        """Redis 연결 오류를 낸다."""
        raise ConnectionError("redis down")

    def delete(self, *_a):
        """Redis 연결 오류를 낸다."""
        raise ConnectionError("redis down")


async def test_token_check_fails_closed_when_redis_errors(client: AsyncClient, monkeypatch):
    """Redis가 오류를 내면 검사를 통과시키지 않고 503인지 확인한다.

    조회 예외를 토큰을 요구할 수 없는 환경과 같은 경우로 보고 검사를 건너뛰면, 첫 배포(미초기화)와 Redis 장애가 겹칠 때 토큰 없이 첫 super_admin이 생긴다.
    """
    from api_gateway import redis_infra

    await _suspend_all_admins()
    email = uniq("no-token")
    try:
        monkeypatch.setattr(redis_infra, "get_redis", lambda: _BrokenRedis())
        r = await client.post("/setup/complete", json={"privacy_consent": True,
            "email": email, "display_name": "토큰 없음",
            "password": "SetupPass!2026x", "token": "",
        })
        assert r.status_code == 503 and r.json()["detail"] == "redis_unavailable", r.text
        async with SessionLocal() as db:
            assert await db.scalar(select(User).where(User.email == email)) is None
    finally:
        await _restore("admin@example.com")


async def test_missing_token_on_real_redis_is_reissued_not_skipped(client: AsyncClient):
    """실제 Redis에 토큰이 없으면 검사를 건너뛰지 않고 새로 발급해 로그에만 출력하는지 확인한다.

    기동 때 발급이 실패했거나 누가 지운 경우다. 검사를 건너뛰면 토큰 없이 관리자가 생긴다.
    """
    from api_gateway import redis_infra

    client_r = redis_infra.get_redis()
    if not hasattr(client_r, "get"):
        return  # 인메모리 폴백이다. 이 환경은 토큰을 요구하지 않는다
    await _suspend_all_admins()
    try:
        client_r.delete(setup_service._TOKEN_KEY)
        r = await client.post("/setup/complete", json={"privacy_consent": True,
            "email": uniq("sneak"), "display_name": "토큰 비움",
            "password": "SetupPass!2026x", "token": "",
        })
        assert r.status_code == 403, r.text
        assert client_r.get(setup_service._TOKEN_KEY), "새 토큰이 발급돼 있어야 한다"
    finally:
        await _restore("admin@example.com")


async def test_concurrent_setup_creates_exactly_one_admin(client: AsyncClient):
    """동시에 두 번 눌러도 관리자가 한 명만 생기는지 확인한다.

    판정과 생성이 한 SQL 문장에서 일어난다.
    """
    await _suspend_all_admins()
    try:
        token = setup_service.issue_token() or ""
        emails = [uniq("race-a"), uniq("race-b")]
        results = await asyncio.gather(*[
            client.post("/setup/complete", json={"privacy_consent": True,
                "email": e, "display_name": "경합", "password": "SetupPass!2026x",
                "token": token,
            }) for e in emails
        ], return_exceptions=True)
        codes = sorted(r.status_code for r in results if hasattr(r, "status_code"))
        assert codes == [201, 409], f"둘 다 통과했다: {codes}"

        async with SessionLocal() as db:
            n = len((await db.scalars(select(User).where(
                User.display_name == "경합", User.approval_status == "active"))).all())
        assert n == 1
    finally:
        await _restore("admin@example.com")


async def test_setup_has_no_unauthenticated_diagnostics(client: AsyncClient):
    """초기화 전에도 최초 설정 경로에 연동 진단이 없는지 확인한다.

    버킷 이름과 워크스페이스 이름은 시스템 내부 정보라서 인증 없는 경로로 내보내지 않는다.
    진단은 초기화 뒤 super_admin이 플랫폼 개요(`/admin/diagnostics/last`)에서 본다.
    """
    await _suspend_all_admins()
    try:
        token = setup_service.issue_token() or ""
        r = await client.post("/setup/diagnostics", json={"token": token})
        assert r.status_code == 404, r.text
    finally:
        await _restore("admin@example.com")


async def test_diagnostics_reports_every_check():
    """연동 진단이 항목을 빠짐없이 내고 판정과 측정값을 채우는지 확인한다."""
    from api_gateway.services.ops import diagnostics
    from starlette.requests import Request

    request = Request({"type": "http", "headers": [], "client": ("203.0.113.5", 1)})
    async with SessionLocal() as s:
        r = await diagnostics.run(s, request=request)
    keys = {c.key for c in r.checks}
    # 이 항목들은 장애가 다른 곳에 드러나지 않는 곳이다.
    # `worker_nodes`: 품질검수·긴 작업 워커가 멈춰도 주기 태스크 기록은 정상이다.
    # `queues`: 멈춘 워커의 큐에 일이 쌓이는 것을 같이 본다.
    # `slot_counter`: 분석 호출 분배가 프로세스 안 카운터로 대체되어도 다른 곳에서는 보이지 않는다.
    # `client_ip`: 앞단 프록시를 신뢰하지 않으면 모든 사용자가 한 주소로 보인다.
    # `backup`: DB 백업이 멈춰도 서비스는 그대로 돈다.
    assert keys == {"db", "redis", "storage", "maiu", "workers", "worker_nodes", "queues",
                    "backup", "slot_counter", "client_ip", "tls"}
    # 판정은 셋 중 하나여야 화면이 색을 정할 수 있다.
    assert {c.status for c in r.checks} <= {"ok", "warn", "fail"}
    # 무엇을 확인했는지가 비어 있으면 진단 결과로 쓸 수 없다.
    assert all(c.detail for c in r.checks)


async def test_diagnostics_counts_registered_connections(monkeypatch):
    """시스템 진단이 외부를 호출하지 않고 등록한 저장소·분석 연결의 수와 점검 실패를 세는지 확인한다.

    자격증명은 프로젝트가 고른 연결에서만 오므로 시스템 수준에서 쓸 버킷이 없다.
    실제 접속 확인은 연결 주기 점검과 프로젝트 대상 점검이 한다.
    연결이 하나도 없으면 실패이고 등록할 곳을 안내한다.
    """
    from api_gateway.config import settings
    from api_gateway.database import SessionLocal
    from api_gateway.services.ops import diagnostics

    async with SessionLocal() as s:
        c = await diagnostics._check_storage(s)
    assert c.status in ("ok", "warn") and "저장소 연결" in c.detail, c
    monkeypatch.setattr(settings, "maiu_enabled", False)
    async with SessionLocal() as s:
        a = await diagnostics._check_analysis(s)
    # 화면(`platform/lib/analysis_off.ts`)이 `분석 연동이 꺼져`로 시작하는 사유를 알아본다.
    assert a.status == "warn" and a.detail.startswith("분석 연동이 꺼져")
    assert "MAIU_ENABLED" in (a.hint or "") and "최고관리자" in (a.hint or "")

    class _NoRows:
        """연결이 하나도 없는 조회 결과."""

        def scalars(self):
            """빈 목록을 돌려준다."""
            return self

        def all(self):
            """빈 목록을 돌려준다."""
            return []

    class _Empty:
        """연결 조회가 빈 세션."""

        async def execute(self, *_a, **_k):
            """빈 결과를 돌려준다."""
            return _NoRows()

    monkeypatch.setattr(settings, "maiu_enabled", True)
    c = await diagnostics._check_storage(_Empty())
    assert c.status == "fail" and c.detail == "등록한 저장소 연결이 없습니다"
    assert "자격증명 연결" in (c.hint or "")
    a = await diagnostics._check_analysis(_Empty())
    assert a.status == "fail" and a.detail == "등록한 분석 연결이 없습니다"


async def test_long_period_tasks_are_not_death(monkeypatch):
    """하루 1회 도는 태스크가 한동안 실행되지 않아도 중단으로 표시하지 않는지 확인한다.

    워커가 동작한다는 근거는 태스크 하나라도 최근에 성공했는지다.
    태스크마다 최근 실행을 요구하면 daily 태스크가 항상 중단으로 표시되고, 항상 빨간 진단은 아무도 보지 않는다.
    """
    import time

    from api_gateway import redis_infra
    from api_gateway.services.ops import diagnostics

    now = int(time.time())

    class _Fake:
        """하트비트 키를 가진 Redis를 흉내 낸다."""
        _keys = {
            "reborn:beat:worker_io.tasks.run_maiu_reconciler": f"{now - 3} 5",
            "reborn:beat:worker_io.tasks.purge_audit_events": f"{now - 80000} 86400",
        }

        def scan_iter(self, match):
            """모든 하트비트 키를 돌려준다."""
            return iter(self._keys)

        def get(self, k):
            """키 값을 돌려준다."""
            return self._keys.get(k)

    monkeypatch.setattr(redis_infra, "get_redis", lambda: _Fake())
    c = diagnostics._check_workers()
    assert c.status == "ok", c.detail
    assert "다음 실행 대기" in c.detail

    # 반대로 모든 태스크가 오래 실행되지 않았으면 중단이다.
    _Fake._keys = {"reborn:beat:worker_io.tasks.run_maiu_reconciler": str(now - 90000)}
    assert diagnostics._check_workers().status == "fail"

    # 백업 하트비트는 워커가 동작한다는 근거로 세지 않는다. 백업은 Celery 밖에서 돈다.
    _Fake._keys = {"reborn:beat:worker_io.tasks.run_maiu_reconciler": str(now - 90000),
                   "reborn:beat:pg-backup": str(now - 3)}
    assert diagnostics._check_workers().status == "fail"


async def test_one_overdue_task_fails_even_if_others_are_fresh(monkeypatch):
    """다른 태스크가 최근에 성공해도 한 태스크가 자기 주기의 3배를 넘게 성공하지 않으면 실패다.

    태스크 하나만 최근이어도 정상으로 보면 30초 주기 투입 태스크가 계속 실패해도
    5초 주기 태스크가 성공하는 한 정상으로 표시된다.
    """
    import time

    from api_gateway import redis_infra
    from api_gateway.services.ops import diagnostics

    now = int(time.time())
    keys = {
        "reborn:beat:worker_io.tasks.run_maiu_reconciler": f"{now - 3} 5",
        "reborn:beat:worker_io.tasks.dispatch_transcodes": f"{now - 200} 30",
        "reborn:beat:worker_io.tasks.purge_audit_events": f"{now - 80000} 86400",
    }

    class _Fake:
        """하트비트 키를 가진 Redis를 흉내 낸다."""

        def scan_iter(self, match):
            """모든 하트비트 키를 돌려준다."""
            return iter(keys)

        def get(self, k):
            """키 값을 돌려준다."""
            return keys.get(k)

    monkeypatch.setattr(redis_infra, "get_redis", lambda: _Fake())
    c = diagnostics._check_workers()
    assert c.status == "fail" and "1개" in c.detail, c.detail
    keys["reborn:beat:worker_io.tasks.dispatch_transcodes"] = f"{now - 60} 30"
    assert diagnostics._check_workers().status == "ok", "주기의 3배 안이면 정상이다"


async def test_worker_nodes_and_queue_lengths(monkeypatch):
    """워커 세 종류의 동작 기록과 큐 길이를 보여 준다. 오래된 기록은 실패, 없는 기록은 주의다."""
    import time

    from api_gateway import redis_infra
    from api_gateway.services.ops import diagnostics

    now = int(time.time())
    keys = {"reborn:node:worker": str(now - 10), "reborn:node:qc": str(now - 20),
            "reborn:node:gpu": str(now - 5)}
    lengths = {"q.transcode": 3, "q.deident": 1}

    class _Fake:
        """노드 기록과 큐 길이를 가진 Redis를 흉내 낸다."""

        def get(self, k):
            """키 값을 돌려준다."""
            return keys.get(k)

        def llen(self, k):
            """큐 길이를 돌려준다."""
            return lengths.get(k, 0)

    monkeypatch.setattr(redis_infra, "get_redis", lambda: _Fake())
    assert diagnostics._check_worker_nodes().status == "ok"
    keys["reborn:node:gpu"] = str(now - 600)
    c = diagnostics._check_worker_nodes()
    assert c.status == "fail" and "긴 작업 워커" in c.detail, c.detail
    del keys["reborn:node:gpu"]
    assert diagnostics._check_worker_nodes().status == "warn"

    q = diagnostics._check_queues()
    assert q.status == "ok"
    assert "변환 3건" in q.detail and "비식별화 1건" in q.detail and "품질검수 0건" in q.detail


async def test_backup_check_by_last_success(monkeypatch):
    """DB 백업 항목이 마지막 성공 시각으로 판정하는지 확인한다.

    주기는 12시간이라 한 번 놓친 것은 정상으로 두고 두 번 놓치면 실패로 바꾼다.
    운영에서 기록이 없으면 백업이 한 번도 돌지 않은 것이라 주의로 표시한다.
    """
    import time

    from api_gateway import redis_infra
    from api_gateway.config import settings
    from api_gateway.services.ops import diagnostics

    now = int(time.time())
    keys: dict[str, str] = {}

    class _Fake:
        """백업 하트비트 키만 가진 Redis를 흉내 낸다."""

        def get(self, k):
            """키 값을 돌려준다."""
            return keys.get(k)

    monkeypatch.setattr(redis_infra, "get_redis", lambda: _Fake())

    # 로컬은 백업을 끈 상태가 기본이라 기록이 없어도 정상이다.
    assert diagnostics._check_backup().status == "ok"
    monkeypatch.setattr(settings, "environment", "prod")
    c = diagnostics._check_backup()
    assert c.status == "warn" and c.hint

    keys["reborn:beat:pg-backup"] = str(now - 13 * 3600)
    c = diagnostics._check_backup()
    assert c.status == "ok" and "13시간 전" in c.detail

    keys["reborn:beat:pg-backup"] = str(now - 26 * 3600)
    c = diagnostics._check_backup()
    assert c.status == "fail" and "26시간 전" in c.detail


async def test_status_does_not_rotate_the_token(client: AsyncClient):
    """상태 조회가 토큰을 새로 만들지 않는지 확인한다.

    `/setup/status`가 발급 함수를 부르면 로그인 화면을 여는 것만으로 로그에 출력된 토큰이 바뀐다.
    그러면 운영자가 방금 복사한 값이 거부된다.
    """
    await _suspend_all_admins()
    try:
        issued = setup_service.issue_token()
        assert issued, "이 테스트는 실 Redis가 있어야 의미가 있다"
        for _ in range(3):
            assert (await client.get("/setup/status")).status_code == 200
        assert setup_service.issue_token() == issued, "조회가 토큰을 갈아치웠다"
        # 그 토큰은 계속 유효해야 한다.
        assert setup_service.token_state(issued) == "ok"
    finally:
        await _restore("admin@example.com")
