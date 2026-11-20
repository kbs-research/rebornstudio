"""자격증명 연결의 주기 점검 테스트. 아무도 화면을 보지 않을 때도 돌고 상태 전이를 기록한다.

규칙은 플랫폼 진단과 같지만 기록 대상이 다르다.
연결은 조직에 속하므로(`Connection.org_id`) 전이는 연결 단위 이벤트(`connection.health_changed`)로 남는다.

이 파일이 보장하는 것:

1. 첫 점검은 전이로 세지 않는다. 비교 대상이 없다.
2. `ok`와 `ok 아님` 사이의 이동만 전이다. `warn`과 `fail` 사이의 이동은 전이로 세지 않는다.
3. 실패와 복구는 전이마다 기록한다. 실패와 복구 기록으로 장애 기간을 정한다.
4. 한 번도 정상이었던 적이 없는 연결의 경고는 전이로 세지 않는다.
5. 점검 결과(`detail`)에 내부 예외 이름이 드러나지 않는다.
"""
from uuid import UUID, uuid4

from api_gateway.database import SessionLocal
from api_gateway.models import Connection, DomainEvent
from api_gateway.services.stacks import connections as conn_service
from httpx import AsyncClient
from reborn_contracts.connection import ConnectionTestResult
from sqlalchemy import select
from tests._helpers import default_org_id, new_project


def _r(status: str, detail: str = "실측") -> ConnectionTestResult:
    """점검 결과 하나를 만든다."""
    return ConnectionTestResult(status=status, detail=detail)


async def _mk_conn(org_id: str, name: str) -> UUID:
    """연결을 하나 만든다. 이름에 난수를 붙인다.

    `uq_connection_org_name` 제약이 있고, conftest 정리는 사용자·프로젝트만 지우고 연결 행은 지우지 않는다.
    그래서 고정 이름을 쓰면 두 번째 실행부터 IntegrityError로 실패한다.
    """
    async with SessionLocal() as s:
        c = Connection(org_id=UUID(org_id), name=f"{name}-{uuid4().hex[:8]}",
                       provider="s3", config={"bucket": "b"})
        s.add(c)
        await s.commit()
        return c.id


async def test_first_check_is_not_a_transition(client: AsyncClient, admin_token: str):
    """비교 대상이 없는 첫 점검을 전이로 보지 않고 결과만 행에 남기는지 확인한다."""
    org = await default_org_id()
    cid = await _mk_conn(org, "conn-first")
    async with SessionLocal() as s:
        c = await s.get(Connection, cid)
        assert await conn_service.record_result(s, c, _r("fail")) is False
        await s.commit()
        c = await s.get(Connection, cid)
        assert c.last_status == "fail" and c.last_checked_at is not None


async def test_warn_to_fail_is_not_an_event(client: AsyncClient, admin_token: str):
    """`warn`(자격증명 미설정)에서 `fail`로 가는 이동을 전이로 보지 않는지 확인한다. 둘 다 정상 아님이다."""
    org = await default_org_id()
    cid = await _mk_conn(org, "conn-warn")
    async with SessionLocal() as s:
        c = await s.get(Connection, cid)
        await conn_service.record_result(s, c, _r("warn"))
        assert await conn_service.record_result(s, c, _r("fail")) is False
        await s.commit()


async def test_recovery_is_recorded(client: AsyncClient, admin_token: str):
    """복구를 전이로 기록하는지 확인한다. 실패와 복구 기록으로 장애 기간을 정한다."""
    org = await default_org_id()
    cid = await _mk_conn(org, "conn-recover")
    async with SessionLocal() as s:
        c = await s.get(Connection, cid)
        await conn_service.record_result(s, c, _r("fail"))
        assert await conn_service.record_result(s, c, _r("ok")) is True
        await s.commit()


async def test_unreachable_endpoint_is_reported_not_raised(client: AsyncClient, admin_token: str):
    """접속하지 못한 결과가 예외가 아니라 점검 결과로 돌아오고 행에 남는지 확인한다.

    프로브는 실패를 보고하는 역할이라서 예외로 끝나면 그 역할을 하지 못한다.
    주기 점검은 이 결과를 행에 남겨야 하고, 예외가 나면 끊긴 자격증명이 기록되지 않는다.
    권한 거절과 문구가 다른지도 함께 확인한다.
    권한 거절은 자격증명 문제이고 접속 실패는 주소·네트워크 문제라서 운영자가 확인할 곳이 다르다.
    `_probe`가 `ClientError`(접속은 했지만 거절)만 잡으면 접속하지 못한 경우의 `EndpointConnectionError`가 그대로 올라가 연결 저장이 500이 된다.
    """
    from api_gateway.services.crypto import encrypt

    org = await default_org_id()
    async with SessionLocal() as s:
        c = Connection(
            org_id=UUID(org), name=f"unreachable-{uuid4().hex[:8]}", provider="s3",
            # 리슨하는 프로세스가 없는 포트라서 접속할 수 없다.
            config={"bucket": "b", "endpoint": "http://127.0.0.1:9", "region": "kr"},
            secret_ciphertext=encrypt("ak\nsk"),
        )
        s.add(c)
        await s.commit()
        cid = c.id

    async with SessionLocal() as s:
        c = await s.get(Connection, cid)
        r = await conn_service.check_saved(s, c)   # 예외가 아니라 결과로 돌아와야 한다
    assert r.status == "fail"
    assert "연결하지 못했" in r.detail, r.detail
    assert r.hint and "자격증명 문제와는" in r.hint

    # 그 실패가 행에 남아야 주기 점검이 장애를 기록한다.
    async with SessionLocal() as s:
        c = await s.get(Connection, cid)
        assert c.last_status == "fail" and c.last_checked_at is not None


async def test_check_does_not_look_like_an_edit(client: AsyncClient, admin_token: str):
    """점검이 `updated_at`을 바꾸지 않는지 확인한다. 점검은 설정 수정과 다른 기록이다.

    `updated_at`에 `onupdate=now()`가 걸려 있어 상태만 써도 갱신된다.
    그러면 아무도 설정을 고치지 않았는데 목록이 "설정 방금 전"을 표시한다.
    운영자는 이 연결을 고친 사람을 찾게 되지만 실제로는 아무도 고치지 않았다.
    """
    org = await default_org_id()
    cid = await _mk_conn(org, "conn-untouched")
    async with SessionLocal() as s:
        c = await s.get(Connection, cid)
        before = c.updated_at
        await conn_service.record_result(s, c, _r("fail"))
        await s.commit()

    async with SessionLocal() as s:
        c = await s.get(Connection, cid)
        assert c.last_status == "fail", "점검 결과는 남아야 한다"
        assert c.updated_at == before, "설정 시각은 그대로여야 한다"


async def test_failure_detail_has_no_internal_names(client: AsyncClient, admin_token: str):
    """실패 사유에 예외 클래스 이름이 없는지 확인한다. 사유는 화면에 나가는 값이다.

    원본 예외 타입은 서버 로그에 남으므로 조사에는 지장이 없다.
    """
    from api_gateway.services.crypto import encrypt

    org = await default_org_id()
    async with SessionLocal() as s:
        c = Connection(
            org_id=UUID(org), name=f"noleak-{uuid4().hex[:8]}", provider="s3",
            config={"bucket": "b", "endpoint": "http://127.0.0.1:9", "region": "kr"},
            secret_ciphertext=encrypt("ak\nsk"),
        )
        s.add(c)
        await s.commit()
        cid = c.id

    async with SessionLocal() as s:
        r = await conn_service.check_saved(s, await s.get(Connection, cid))
    assert r.status == "fail"
    assert "Error" not in r.detail, r.detail
    assert r.detail == "엔드포인트에 연결하지 못했습니다", r.detail


async def test_list_says_which_projects_use_the_connection(
    client: AsyncClient, admin_token: str
):
    """연결 목록이 그 연결을 실제로 쓰는 프로젝트를 함께 내는지 확인한다.

    연결 삭제는 되돌릴 수 없다(시크릿을 다시 보여 주지 않는다).
    그래서 삭제 확인창이 쓰는 스택이 있는지 보여 줄 수 있어야 한다.
    캡션 QC 설정이 연결을 이름으로 가리키므로(`caption_qc.connection_name`) 연결을 쓰는 프로젝트를 알 수 있다.
    이름 참조라서 이름에 오타가 있으면 어느 연결과도 이어지지 않는다. 이 경우도 테스트로 보장한다.
    없는 이름을 가리키는 프로젝트는 그 연결의 `used_by`에 들어가지 않는다.
    """
    import uuid as _uuid

    from api_gateway.models import Project

    org = await default_org_id()
    cid = await _mk_conn(org, "conn-used")
    async with SessionLocal() as s:
        conn = await s.get(Connection, cid)
        conn_name = conn.name

    pid_used = await new_project(client, admin_token, "uses-conn")
    pid_typo = await new_project(client, admin_token, "typo-conn")
    async with SessionLocal() as s:
        for pid, ref in ((pid_used, conn_name), (pid_typo, f"{conn_name}-오타")):
            p = await s.get(Project, _uuid.UUID(pid))
            p.settings = {**(p.settings or {}), "caption_qc": {"connection_name": ref}}
        await s.commit()
        # 도우미가 이름에 난수를 붙이므로 실제 이름을 읽어 비교한다.
        used_name = (await s.get(Project, _uuid.UUID(pid_used))).name

    rows = (await client.get(
        "/connections", headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    row = next(r for r in rows if r["id"] == str(cid))
    assert row["used_by"] == [used_name], row["used_by"]   # 오타 프로젝트는 들어가지 않는다

    # 아무도 가리키지 않는 연결은 빈 목록이다. 빈 목록은 사용처가 없다는 뜻이다.
    other = await _mk_conn(org, "conn-unused")
    rows = (await client.get(
        "/connections", headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert next(r for r in rows if r["id"] == str(other))["used_by"] == []

    # 쓰는 연결은 지우지 않는다.
    # 지우면 그 프로젝트의 캡션 품질검수가 나중에 모델을 부르지 못하고 조용히 건너뛴다.
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.delete(f"/connections/{cid}", headers=h)
    assert r.status_code == 409 and r.json()["detail"] == "connection_in_use", r.text
    async with SessionLocal() as s:
        assert await s.get(Connection, cid) is not None, "막았으면 남아 있어야 한다"
    # 아무도 쓰지 않는 연결은 지워진다. 없는 연결은 404다.
    assert (await client.delete(f"/connections/{other}", headers=h)).status_code == 204
    assert (await client.delete(f"/connections/{other}", headers=h)).status_code == 404


# 전이 기록


async def test_flapping_is_recorded_each_time(client: AsyncClient, admin_token: str):
    """ok와 실패를 한 회차씩 오가면 기록이 전이마다 남는지 확인한다."""
    org = await default_org_id()
    cid = await _mk_conn(org, "conn-flap")
    async with SessionLocal() as s:
        c = await s.get(Connection, cid)
        await conn_service.record_result(s, c, _r("ok"))
        for st in ("fail", "ok", "fail", "ok", "fail", "ok"):
            assert await conn_service.record_result(s, c, _r(st)) is True
        await s.commit()

        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "connection.health_changed",
            DomainEvent.target_id == cid))).all()
        assert len(ev) == 6, "상태가 바뀔 때마다 기록이 남아야 언제부터 깨졌는지 알 수 있다"


async def test_never_ok_connection_is_not_an_incident(client: AsyncClient, admin_token: str):
    """한 번도 정상이었던 적이 없는 연결(자격증명 미설정 등)은 계속 `warn`이어도 전이로 기록하지 않는지 확인한다."""
    org = await default_org_id()
    cid = await _mk_conn(org, "conn-never")
    async with SessionLocal() as s:
        c = await s.get(Connection, cid)
        for _ in range(4):
            await conn_service.record_result(s, c, _r("warn"))
        await s.commit()
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "connection.health_changed",
            DomainEvent.target_id == cid))).all()
        assert ev == [], "한 번도 정상이 아니었던 연결의 경고는 전이가 아니다"


async def test_bedrock_rejection_is_explained_without_the_aws_code(monkeypatch):
    """Bedrock 거절 사유에 AWS 오류 코드를 싣지 않는지 확인한다.

    사유는 의미와 확인할 곳을 한국어로 쓴다.
    서명 거절은 키가 틀렸을 때도, 서버 시각이 어긋났을 때도 나므로 힌트가 두 경우를 모두 안내해야 한다.
    """
    import boto3
    from botocore.exceptions import ClientError

    class _Client:
        """서명 거절만 내는 Bedrock 클라이언트 대역."""
        def list_foundation_models(self):
            """서명 거절 오류를 낸다."""
            raise ClientError(
                {"Error": {"Code": "InvalidSignatureException", "Message": "sig"}},
                "ListFoundationModels")

    seen = {}

    def _fake_client(*args, **kwargs):
        """클라이언트 생성 인자를 기록하고 대역을 돌려준다."""
        seen.update(kwargs)
        return _Client()

    monkeypatch.setattr(boto3, "client", _fake_client)
    conn = Connection(org_id=uuid4(), name="bedrock-x", provider="bedrock",
                      config={"region": "us-east-1", "model": "m"})
    r = await conn_service._probe_bedrock(conn, "ak", "sk")
    assert r.status == "fail"
    assert "Exception" not in r.detail and "InvalidSignature" not in r.detail, r.detail
    assert r.detail == "Bedrock 조회 실패 · 자격증명이 거부됐습니다"
    assert r.hint and "서버 시각" in r.hint
    # 막힌 네트워크에서 몇 분씩 멈추지 않게 짧은 타임아웃과 재시도 1회를 쓴다.
    cfg = seen["config"]
    assert cfg.connect_timeout == 5 and cfg.retries == {"max_attempts": 1}
