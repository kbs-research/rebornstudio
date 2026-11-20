"""연동 점검이 아무도 화면을 보지 않을 때도 돌고, 바뀐 상태만 기록하는지 보는 테스트.

화면은 진단을 돌리지 않고 마지막 결과를 읽는다.
주기 점검(`worker_io.tasks.check_integrations`)은 15분마다 돈다.
그래서 아무도 화면을 보지 않는 동안 자격증명이 무효가 되어도 전이가 감사 기록에 남는다.
보장하는 것은 네 가지다.

1. 첫 실행은 전이로 세지 않는다. 비교할 이전 상태가 없다.
   첫 실행을 모두 바뀐 것으로 보면 실행할 때마다 전이 기록이 쏟아진다.
2. `ok`와 `ok 아님` 사이가 바뀔 때만 전이다. `warn`과 `fail` 사이의 이동은 전이로 세지 않는다.
   이 이동까지 기록하면 실제 장애 기록이 묻힌다.
3. 깨지거나 복구되면 전이 이벤트(`system.integration_changed`)를 남긴다.
   기록이 없으면 장애가 언제 시작되고 끝났는지 알 수 없다.
4. 한 번도 돌지 않았으면 `None`이다. 정상으로 표시하지 않는다.

같은 DB의 다른 테스트도 이벤트를 남기므로 이벤트는 절대 수가 아니라 증분으로 센다.
"""
import contextlib
import uuid

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent
from api_gateway.services.ops import diagnostics as diag
from api_gateway.services.ops.diagnostics import Check, Diagnostics
from sqlalchemy import func, select

#: 테스트마다 바꾸는 항목 이름의 접미사.
_TAG = {"v": ""}


@pytest.fixture(autouse=True)
def _clean_snapshot():
    """테스트마다 앞뒤로 스냅숏 키를 지운다. 스냅숏은 Redis 전역 키라서 테스트 사이에 공유된다."""
    from api_gateway import redis_infra

    def _drop():
        """스냅숏 키를 지운다."""
        # Redis가 없으면 스냅숏도 없으므로 지우기 실패는 무시한다.
        with contextlib.suppress(Exception):
            redis_infra.get_redis().delete(diag._SNAPSHOT_KEY)

    _TAG["v"] = uuid.uuid4().hex[:6]
    _drop()
    yield
    _drop()


def _diag(*pairs: tuple[str, str]) -> Diagnostics:
    """항목 키와 상태 쌍으로 진단 결과를 만든다."""
    return Diagnostics(checks=[
        Check(key=k, label=f"{k.upper()}-{_TAG['v']}", status=s, detail=f"{k} {s}")
        for k, s in pairs
    ])


async def _events(s) -> int:
    """증분 비교에 쓰는 연동 전이 이벤트 수를 센다."""
    ev = await s.scalar(select(func.count()).select_from(DomainEvent).where(
        DomainEvent.event_type == "system.integration_changed"))
    return int(ev or 0)


async def test_first_run_is_not_a_transition(client, admin_token):
    """비교할 이전 상태가 없는 첫 실행을 전이로 보지 않고 스냅숏만 남기는지 확인한다."""
    async with SessionLocal() as s:
        changed = await diag.record(s, _diag(("storage", "fail")))
    assert changed == []
    snap = diag.last_snapshot()
    assert snap is not None and snap["failed"] == 1


async def test_ok_to_fail_is_recorded(client, admin_token):
    """정상이던 항목이 깨지면 전이 이벤트를 남기는지 확인한다."""
    async with SessionLocal() as s:
        ev0 = await _events(s)
        await diag.record(s, _diag(("storage", "ok")))
        changed = await diag.record(s, _diag(("storage", "fail")))
        await s.commit()

        assert [c["key"] for c in changed] == ["storage"]
        assert changed[0]["before"] == "ok" and changed[0]["after"] == "fail"

        assert await _events(s) == ev0 + 1, "상태가 바뀐 순간이 감사 기록에 남아야 한다"


async def test_warn_to_fail_is_not_an_event(client, admin_token):
    """`warn`에서 `fail`로 가는 이동을 전이로 보지 않는지 확인한다. 두 상태 모두 정상 아님이다.

    둘을 구분하면 자격증명 미등록(warn)에서 버킷 없음(fail)으로 가는 이동까지 장애로 기록한다.
    두 상태 모두 설정이 끝나지 않은 상태다.
    """
    async with SessionLocal() as s:
        await diag.record(s, _diag(("storage", "warn")))
        changed = await diag.record(s, _diag(("storage", "fail")))
    assert changed == []


async def test_recovery_is_recorded(client, admin_token):
    """복구를 이벤트로 남기는지 확인한다. 깨진 기간은 장애 이벤트와 복구 이벤트로 정해진다."""
    async with SessionLocal() as s:
        await diag.record(s, _diag(("storage", "fail")))
        ev0 = await _events(s)
        changed = await diag.record(s, _diag(("storage", "ok")))
        await s.commit()

        assert changed[0]["before"] == "fail" and changed[0]["after"] == "ok"
        assert await _events(s) == ev0 + 1, "복구도 기록은 남는다"


async def test_last_snapshot_is_none_before_any_run():
    """한 번도 돌지 않았으면 마지막 스냅숏이 `None`인지 확인한다. 정상으로 표시하지 않는다."""
    assert diag.last_snapshot() is None


async def test_failure_detail_has_no_exception_class_name():
    """점검 줄에 예외 클래스 이름이 없는지 확인한다.

    저장소 쓰기 점검은 프로젝트 저장소 대상 점검(`project_storage`)이 한다.
    `… 쓰기 실패 — EndpointConnectionError` 같은 문자열이 그대로 화면의 점검 줄이 되면 운영자가 원인을 읽을 수 없다.
    """
    from api_gateway.services.stacks import project_storage
    from botocore.exceptions import ClientError, EndpointConnectionError

    r = project_storage._failure("storage_write",
                                 EndpointConnectionError(endpoint_url="http://127.0.0.1:9"))
    assert "Error" not in r.check.detail, r.check.detail
    assert r.check.detail.endswith("엔드포인트에 연결하지 못했습니다"), r.check.detail

    r = project_storage._failure(
        "storage_write", ClientError({"Error": {"Code": "SignatureDoesNotMatch"}}, "PutObject"))
    assert "SignatureDoesNotMatch" not in r.check.detail and r.check.status == "fail"


def test_slot_counter_fallback_is_visible():
    """분석 호출 분배가 프로세스 안 카운터로 대체되면 진단이 경고하는지 확인한다.

    한 번 대체되면 프로세스가 끝날 때까지 대체된 카운터를 쓴다.
    증상은 재시도가 가끔 늦어지는 것뿐이라서 진단 밖에서는 드러나지 않는다.
    진단은 상태를 바꾸지 않으므로 카운터를 새로 만들지 않는다.
    """
    from api_gateway.services.maiu import slot_counter

    prev = slot_counter._counter
    try:
        slot_counter.set_counter(slot_counter._LRUSlotCounter())
        c = diag._check_slot_counter()
        assert c.status == "warn" and "레플리카" in (c.hint or "")

        slot_counter.set_counter(None)
        c = diag._check_slot_counter()
        assert c.status in ("ok", "warn")
        assert slot_counter._counter is None, "진단이 카운터를 만들었다"
    finally:
        slot_counter.set_counter(prev)


def _req(client: str, xff: str | None = None, proto: str | None = None):
    """접속 주소와 전달 헤더만 있는 최소 요청을 만든다.

    `proto`는 uvicorn이 신뢰 프록시의 `X-Forwarded-Proto`를 반영한 뒤의 요청 scheme으로 넣는다.
    """
    from starlette.requests import Request

    headers = []
    if xff:
        headers.append((b"x-forwarded-for", xff.encode()))
    return Request({"type": "http", "headers": headers, "client": (client, 1),
                    "scheme": proto or "http"})


def test_client_ip_check_catches_an_untrusted_front_proxy():
    """서버가 앞단 프록시를 신뢰하지 않는 설정을 진단이 경고하는지 확인한다.

    그 설정이면 모든 사용자가 한 주소로 보인다.
    주소 값은 개인정보라서 결과에 싣지 않는다. 점검을 실행한 사용자 자신의 요청으로 판정한다.
    """
    # 프록시 없이 직접 접속한 경우다. 판정할 것이 없다.
    assert diag._check_client_ip(_req("203.0.113.5")).status == "ok"
    # 서버가 앞단을 신뢰하지 않아 헤더를 버린 경우다. client는 여전히 nginx의 사설 주소다.
    c = diag._check_client_ip(_req("172.18.0.3", xff="203.0.113.5"))
    assert c.status == "warn" and "FORWARDED_ALLOW_IPS" in (c.hint or "")
    assert "172.18" not in c.detail and "203.0" not in c.detail, "주소를 싣지 않는다"
    # 신뢰하는 프록시가 넘긴 사용자 주소로 바뀐 경우다. uvicorn이 client를 바꾼 뒤의 모양이다.
    assert diag._check_client_ip(_req("203.0.113.5", xff="203.0.113.5")).status == "ok"
    # 신뢰하지 않은 LB(사설)를 사용자로 읽은 경우다. 그 왼쪽에 공인 주소가 있다.
    # 파이썬은 문서용 예시 대역 203.0.113.0/24를 사설로 판정하므로 이 경우에는 사설로 판정하지 않는
    # 공유 주소 대역 100.64.0.0/10을 쓴다.
    c = diag._check_client_ip(_req("10.0.0.7", xff="100.64.0.8, 10.0.0.7"))
    assert c.status == "warn"
    # 사내망 사용자(사설 주소뿐)는 정상이다. 사설 주소라는 이유만으로 경고하지 않는다.
    assert diag._check_client_ip(_req("10.0.0.9", xff="10.0.0.9")).status == "ok"


async def test_periodic_run_carries_request_scoped_checks(client, admin_token, monkeypatch):
    """주기 점검이 요청이 없는 TLS·클라이언트 주소 판정을 마지막 화면 점검 결과로 이어 쓰는지 확인한다.

    주기 점검이 요청 없이 TLS를 판정하면 항상 `warn`이고 화면 점검은 `ok`다.
    그러면 두 점검이 번갈아 돌 때마다 실제로는 없는 전이 기록이 생긴다.
    """
    from api_gateway.config import settings

    monkeypatch.setattr(settings, "environment", "prod")
    async with SessionLocal() as s:
        # 사용자가 https 앞단을 거쳐 실행한 점검이다. TLS ok가 스냅숏에 남는다.
        await diag.run_and_record(s, request=_req("203.0.113.5", proto="https"))
        # 요청이 없는 주기 점검이다. 판정을 이어 쓰므로 전이가 없다.
        _d, changed = await diag.run_and_record(s)
        await s.rollback()
    tls = next(c for c in _d.checks if c.key == "tls")
    assert tls.status == "ok" and "이어 씁니다" in (tls.hint or "")
    assert all(c["key"] not in ("tls", "client_ip") for c in changed), changed


async def test_carried_check_without_history_is_unchecked_not_warn(client, admin_token):
    """이어 쓸 판정이 없는 항목이 `warn`이 아니라 `unchecked`(확인 전)인지 확인한다.

    `warn`으로 두면 아무도 점검하지 않은 배포의 개요가 항상 `1건 주의`를 띄운다.
    확인 전과 다른 상태 사이의 이동은 연동 상태가 바뀐 것이 아니므로 전이로 보지 않는다.
    """
    c = diag._carried("client_ip", "클라이언트 주소")
    assert c.status == "unchecked" and c.hint, c

    async with SessionLocal() as s:
        await diag.record(s, _diag(("client_ip", "ok")))
        changed = await diag.record(s, _diag(("client_ip", "unchecked")))
        assert changed == []
        changed = await diag.record(s, _diag(("client_ip", "fail")))
        assert changed == [], "확인 전에서 실패로 가는 것도 전이가 아니다"


async def test_snapshot_keeps_the_hint(client, admin_token):
    """스냅숏에 고치는 법(`hint`)이 남는지 확인한다. 화면은 마지막 스냅숏을 그대로 그린다."""
    async with SessionLocal() as s:
        await diag.record(s, Diagnostics(checks=[
            Check(key="storage", label="S", status="fail", detail="d", hint="고치는 법")]))
    snap = diag.last_snapshot()
    assert snap is not None and snap["checks"][0]["hint"] == "고치는 법"


async def test_overview_judges_request_checks_from_its_own_request(client, admin_token, monkeypatch):
    """플랫폼 개요가 읽는 마지막 결과에서 TLS·클라이언트 주소를 개요 요청으로 다시 판정하는지 확인한다.

    주기 점검에는 요청이 없어 두 항목이 확인 전으로 남는다. 개요가 그대로 그리면 TLS 미적용 경고가
    어디에도 나타나지 않는다. 다시 판정해도 스냅숏 자체는 바꾸지 않는다.
    """
    from api_gateway.config import settings

    monkeypatch.setattr(settings, "environment", "prod")
    async with SessionLocal() as s:
        await diag.run_and_record(s)   # 요청 없는 주기 점검
        await s.commit()
    snap = diag.last_snapshot()
    assert snap is not None
    assert {c["key"]: c["status"] for c in snap["checks"]}["tls"] == "unchecked"

    r = await client.get("/admin/diagnostics/last",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    tls = next(c for c in r.json()["checks"] if c["key"] == "tls")
    assert tls["status"] == "warn", tls   # 테스트 클라이언트는 http로 접속한다
    client_ip = next(c for c in r.json()["checks"] if c["key"] == "client_ip")
    assert client_ip["status"] == "ok"
    after = diag.last_snapshot()
    assert {c["key"]: c["status"] for c in after["checks"]}["tls"] == "unchecked"

    https = diag.with_request_checks(snap["checks"], _req("203.0.113.5", proto="https"))
    assert next(c for c in https if c["key"] == "tls")["status"] == "ok"
    assert len(https) == len(snap["checks"])


async def test_manual_diagnostics_route_is_gone(client, admin_token):
    """사람이 실행하던 진단 라우트가 없는지 확인한다. 진단은 주기 점검만 돌린다."""
    r = await client.get("/admin/diagnostics", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code in (404, 405), r.text
