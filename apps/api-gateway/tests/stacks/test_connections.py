"""자격증명 연결 관리 테스트. 연결은 기본 조직 한 곳에 속하고 시크릿은 쓰기 전용이다.

이 파일은 네 가지를 보장한다.
시크릿은 어떤 응답에도 나오지 않는다.
시크릿이 없는 것과 비운 것을 구분한다.
super_admin만 다룰 수 있다.
저장한 연결은 실제로 접속해 검증한다.
"""
import uuid

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Connection
from api_gateway.services import crypto
from httpx import AsyncClient
from sqlalchemy import select
from tests import _storage
from tests._helpers import add_member, default_org_id, new_project


def _h(token):
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def test_crypto_round_trips_and_is_not_plaintext():
    """암호문이 평문이 아니고 복호화하면 원래 값인지 확인한다. DB만 유출되어도 시크릿은 유출되지 않게 한다."""
    token = crypto.encrypt("super-secret-key")
    assert "super-secret-key" not in token
    assert crypto.decrypt(token) == "super-secret-key"


async def test_secret_never_comes_back_in_any_response(client: AsyncClient, admin_token: str):
    """시크릿이 어떤 응답에도 나오지 않는지(쓰기 전용) 확인한다.

    create·list 응답 전체를 문자열로 바꿔 시크릿이 없는지 확인한다.
    """
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": f"AWS 서울-{uuid.uuid4().hex[:8]}", "provider": "ncp",
        "config": {"endpoint": "https://s3.example", "bucket": "b", "region": "kr"},
        "access_key": "AKIA_VISIBLE", "secret_key": "SECRET_MUST_HIDE",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_secret"] is True and body["secret_set_at"]
    assert "SECRET_MUST_HIDE" not in r.text and "AKIA_VISIBLE" not in r.text

    lst = await client.get("/connections", headers=_h(admin_token))
    assert "SECRET_MUST_HIDE" not in lst.text and "AKIA_VISIBLE" not in lst.text
    # DB에는 평문이 아니라 암호문으로 저장된다.
    async with SessionLocal() as s:
        c = (await s.scalars(select(Connection).where(Connection.id == uuid.UUID(body["id"])))).one()
    assert c.secret_ciphertext and "SECRET_MUST_HIDE" not in c.secret_ciphertext
    assert crypto.decrypt(c.secret_ciphertext).endswith("SECRET_MUST_HIDE")


async def test_can_create_without_a_secret_then_fill_it(client: AsyncClient, admin_token: str):
    """시크릿 없이 만들면 `has_secret=False`로 보이고 나중에 채울 수 있는지 확인한다."""
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": f"나중에 채움-{uuid.uuid4().hex[:8]}", "provider": "ncp", "config": {"bucket": "b"},
    })
    cid = r.json()["id"]
    assert r.json()["has_secret"] is False

    up = await client.patch(f"/connections/{cid}", headers=_h(admin_token),
                            json={"access_key": "A", "secret_key": "S"})
    assert up.json()["has_secret"] is True


async def test_update_without_secret_keeps_the_existing_one(client: AsyncClient, admin_token: str):
    """시크릿을 보내지 않으면 기존 시크릿을 그대로 두는지 확인한다.

    다른 값 하나를 고칠 때 시크릿을 다시 넣지 않아도 된다.
    """
    cid = (await client.post("/connections", headers=_h(admin_token), json={
        "name": f"keep-{uuid.uuid4().hex[:8]}", "provider": "ncp", "config": {"bucket": "b"},
        "access_key": "A", "secret_key": "S",
    })).json()["id"]
    # config만 바꾼다
    up = await client.patch(f"/connections/{cid}", headers=_h(admin_token),
                            json={"config": {"bucket": "b2"}})
    assert up.json()["has_secret"] is True and up.json()["config"]["bucket"] == "b2"


async def test_clear_secret_is_distinct_from_not_sending_it(client: AsyncClient, admin_token: str):
    """`clear_secret=True`가 시크릿을 비우고, 시크릿을 보내지 않은 경우와 구분되는지 확인한다."""
    cid = (await client.post("/connections", headers=_h(admin_token), json={
        "name": f"clear-{uuid.uuid4().hex[:8]}", "provider": "ncp", "config": {"bucket": "b"},
        "access_key": "A", "secret_key": "S",
    })).json()["id"]
    up = await client.patch(f"/connections/{cid}", headers=_h(admin_token),
                            json={"clear_secret": True})
    assert up.json()["has_secret"] is False and up.json()["secret_set_at"] is None


async def test_duplicate_name_is_rejected(client: AsyncClient, admin_token: str):
    """같은 이름의 연결이 있으면 409인지 확인한다. 프리셋이 연결을 이름으로 가리킨다."""
    body = {"name": f"dup-{uuid.uuid4().hex[:8]}", "provider": "ncp", "config": {"bucket": "b"}}
    first = await client.post("/connections", headers=_h(admin_token), json=body)
    assert first.status_code == 201, first.text
    r = await client.post("/connections", headers=_h(admin_token), json=body)
    assert r.status_code == 409 and r.json()["detail"] == "name_exists"


async def test_only_super_admin(client: AsyncClient, admin_token: str):
    """연결 관리를 super_admin만 할 수 있는지 확인한다. 자격증명은 가장 민감한 값이라 진단과 같은 권한 등급이다."""
    pid = await new_project(client, admin_token, "conn-perm")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    r = await client.get("/connections", headers=_h(annot))
    assert r.status_code == 403


async def test_save_without_secret_records_warn(client: AsyncClient, admin_token: str):
    """자격증명 없이 저장하면 접속하지 않고 warn을 남기는지 확인한다. 장애가 아니라 설정이 끝나지 않은 상태다."""
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": f"notest-{uuid.uuid4().hex[:8]}", "provider": "ncp", "config": {"bucket": "b"},
    })
    assert r.status_code == 201, r.text
    assert r.json()["check"]["status"] == "warn"
    assert r.json()["last_status"] == "warn" and r.json()["last_checked_at"]


async def test_save_probes_the_real_bucket(client: AsyncClient, admin_token: str):
    """시크릿이 있으면 저장하는 요청에서 실제로 접속해(moto) 검증하는지 확인한다.

    conftest의 인프로세스 moto에 테스트 버킷이 있으므로 그 자격증명으로 만든 연결은 `ok`여야 한다.
    저장할 때 접속까지 확인해야 새 연결을 프로젝트에서 바로 고를 수 있다.
    """
    import contextlib

    from api_gateway.services.media.storage import _client
    # head_bucket이 ok를 돌려주려면 버킷이 있어야 한다. 없으면 만든다.
    with contextlib.suppress(Exception):
        _client().create_bucket(Bucket=_storage.BUCKET)

    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": f"moto-{uuid.uuid4().hex[:8]}", "provider": "ncp",
        "config": {"endpoint": _storage.ENDPOINT, "bucket": _storage.BUCKET,
                   "region": "kr-standard"},
        "access_key": "testing",
        "secret_key": "testing",
    })
    assert r.status_code == 201, r.text
    assert r.json()["check"]["status"] == "ok", r.json()
    assert r.json()["last_status"] == "ok"
    # 행에도 남아야 프로젝트 선택과 플랫폼 개요가 같은 상태를 읽는다.
    async with SessionLocal() as s:
        row = await s.get(Connection, uuid.UUID(r.json()["id"]))
    assert row.last_status == "ok" and row.last_checked_at is not None


async def test_failed_save_check_keeps_the_connection(client: AsyncClient, admin_token: str):
    """저장 직후 점검이 실패해도 연결은 저장되고 실패와 고치는 방법이 응답에 실리는지 확인한다.

    주기 점검과 같은 규칙이다. 점검 실패는 연결 행의 상태로 남고 저장은 그대로 받는다.
    고친 뒤 다시 저장하면 같은 요청에서 다시 점검해 상태가 바뀐다.
    """
    import contextlib

    from api_gateway.services.media.storage import _client
    with contextlib.suppress(Exception):
        _client().create_bucket(Bucket=_storage.BUCKET)

    name = f"badbucket-{uuid.uuid4().hex[:8]}"
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": name, "provider": "ncp",
        "config": {"endpoint": _storage.ENDPOINT, "bucket": "no-such-bucket",
                   "region": "kr-standard"},
        "access_key": "testing", "secret_key": "testing",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["check"]["status"] == "fail" and body["check"]["hint"], body
    assert body["last_status"] == "fail" and body["last_detail"] == body["check"]["detail"]
    listed = (await client.get("/connections", headers=_h(admin_token))).json()
    assert any(c["name"] == name and c["last_status"] == "fail" for c in listed)

    up = await client.patch(f"/connections/{body['id']}", headers=_h(admin_token), json={
        "config": {"endpoint": _storage.ENDPOINT, "bucket": _storage.BUCKET,
                   "region": "kr-standard"},
    })
    assert up.status_code == 200, up.text
    assert up.json()["check"]["status"] == "ok" and up.json()["last_status"] == "ok"


async def test_manual_check_route_is_gone(client: AsyncClient, admin_token: str):
    """사람이 누르던 점검 라우트가 없는지 확인한다. 점검은 저장할 때와 주기 점검에서만 돈다."""
    cid = (await client.post("/connections", headers=_h(admin_token), json={
        "name": f"noroute-{uuid.uuid4().hex[:8]}", "provider": "ncp", "config": {"bucket": "b"},
    })).json()["id"]
    r = await client.post(f"/connections/{cid}/test", headers=_h(admin_token))
    assert r.status_code in (404, 405), r.text


async def test_platform_overview_counts_connection_health(client: AsyncClient, admin_token: str):
    """플랫폼 개요가 자격증명 연결의 마지막 점검 결과를 따로 세는지 확인한다.

    행에 남은 결과를 세기만 하고 새로 접속하지 않는다. 다른 테스트가 만든 연결도 함께 세므로 증가분으로 확인한다.
    `.env` 연동 점검만 세면 연결 화면에 실패한 행이 있어도 개요는 모두 정상으로 표시한다.

    상태는 쓰는 곳이 있는 연결만 센다. 쓰는 곳이 없는 연결은 실패여도 `unused`로만 센다.
    아무 기능도 쓰지 않는 테스트 연결의 실패를 개요의 실패로 띄우지 않는다.
    """
    from api_gateway.models import Project

    org = await default_org_id()
    # 프로젝트를 먼저 만들고 나서 기준 값을 읽는다. 프로젝트 생성은 테스트 저장소 연결을 저장소 대상으로 고르므로,
    # 아무 프로젝트도 없는 DB에서는 그 연결이 쓰는 곳 없음에서 쓰는 곳 있음으로 옮겨 증가분이 어긋난다.
    pids = [await new_project(client, admin_token, label) for label in ("hs-uses-fail", "hs-uses-new")]
    before = (await client.get("/admin/connections/health", headers=_h(admin_token))).json()

    used_fail = f"hs-fail-{uuid.uuid4().hex[:8]}"
    used_new = f"hs-new-{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as s:
        s.add(Connection(org_id=uuid.UUID(org), name=used_fail,
                         provider="bedrock", config={}, last_status="fail"))
        s.add(Connection(org_id=uuid.UUID(org), name=used_new,
                         provider="bedrock", config={}))
        # 아무 프로젝트도 가리키지 않는 테스트 연결. 실패해도 개요의 실패로 세지 않는다.
        s.add(Connection(org_id=uuid.UUID(org), name=f"hs-idle-{uuid.uuid4().hex[:8]}",
                         provider="s3", config={}, last_status="fail"))
        await s.commit()
    for pid, ref in zip(pids, (used_fail, used_new), strict=True):
        async with SessionLocal() as s:
            p = await s.get(Project, uuid.UUID(pid))
            p.settings = {**(p.settings or {}), "caption_qc": {"connection_name": ref}}
            await s.commit()

    after = (await client.get("/admin/connections/health", headers=_h(admin_token))).json()
    assert after["fail"] == before["fail"] + 1
    assert after["unchecked"] == before["unchecked"] + 1, "점검 전은 정상이 아니다"
    assert after["unused"] == before["unused"] + 1, "쓰는 곳이 없는 실패는 실패로 세지 않는다"
    assert after["total"] == before["total"] + 3
    assert after["total"] == (after["ok"] + after["warn"] + after["fail"]
                              + after["unchecked"] + after["unused"])
    # 개요에 이름으로 보이는 문제 목록이다. 쓰는 곳이 있는 실패만 오고, 점검 전·쓰는 곳 없음은 오지 않는다.
    mine = {p["name"]: p for p in after["problems"]}
    assert used_fail in mine, after["problems"]
    assert mine[used_fail]["status"] == "fail"
    assert mine[used_fail]["used_by"]
    assert used_new not in mine, "점검 전은 문제 목록에 오지 않는다"
    assert not any(n.startswith("hs-idle-") for n in mine), "쓰는 곳이 없는 실패는 문제 목록에 오지 않는다"
    ranks = [{"fail": 0, "warn": 1}[p["status"]] for p in after["problems"]]
    assert ranks == sorted(ranks), "실패가 주의보다 먼저 온다"

    pid = await new_project(client, admin_token, "conn-health-perm")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    assert (await client.get("/admin/connections/health", headers=_h(annot))).status_code == 403


@pytest.mark.real_probe
async def test_llm_check_reads_the_same_key_as_caption_qc(monkeypatch):
    """모델 연결 점검이 캡션 품질검수와 같은 값(앞 값)을 API 키로 쓰는지 확인한다.

    실행기는 저장된 두 값 중 앞 값을 `ModelConfig.api_key`로 넘긴다(`caption_qc.runner`).
    점검이 뒤 값을 읽으면 점검은 정상인데 품질검수는 키 없이 호출하게 된다.
    """
    from api_gateway.services.stacks import connections as conn_service

    seen: list[str] = []

    async def _fake_probe_llm(conn, api_key):
        """받은 API 키를 기록하고 성공을 돌려준다."""
        seen.append(api_key)
        return conn_service.ConnectionTestResult(status="ok", detail="ok")

    monkeypatch.setattr(conn_service, "_probe_llm", _fake_probe_llm)
    conn = Connection(org_id=uuid.uuid4(), name="llm-x", provider="openai", config={"model": "m"})
    r = await conn_service._probe(conn, "API_KEY\n")
    assert r.status == "ok" and seen == ["API_KEY"]

    # 뒤 값에만 키가 있으면 품질검수가 쓸 수 없는 연결이다. 점검도 실패로 알린다.
    r = await conn_service._probe(conn, "\nONLY_IN_SECRET")
    assert r.status == "fail" and r.detail == "API 키를 읽지 못했습니다"
    assert seen == ["API_KEY"], "뒤 값을 API 키로 쓰지 않는다"


async def test_name_cannot_be_changed_on_update(client: AsyncClient, admin_token: str):
    """수정 요청이 다른 이름을 보내면 422이고 이름은 그대로이며, 같은 이름은 받는지 확인한다.

    프로젝트 설정(품질검수·비식별화)이 연결을 이름으로 가리킨다.
    이름이 바뀌면 그 설정이 끊겨 비식별화가 연결을 찾지 못한다. 편집 창도 이름 수정을 막는다.
    """
    name = f"이름고정-{uuid.uuid4().hex[:8]}"
    cid = (await client.post("/connections", headers=_h(admin_token), json={
        "name": name, "provider": "ncp",
        "config": {"endpoint": "https://s3.example", "bucket": "b", "region": "kr"},
    })).json()["id"]
    r = await client.patch(
        f"/connections/{cid}", headers=_h(admin_token), json={"name": f"{name}-새"},
    )
    assert (r.status_code, r.json()["detail"]) == (422, "connection_name_immutable")
    same = await client.patch(
        f"/connections/{cid}", headers=_h(admin_token),
        json={"name": name, "config": {"endpoint": "https://s3.example", "bucket": "b2", "region": "kr"}},
    )
    assert same.status_code == 200, same.text
    assert same.json()["name"] == name and same.json()["config"]["bucket"] == "b2"


async def test_new_connection_takes_only_ncp_storage(client: AsyncClient, admin_token: str):
    """새 연결은 저장소로 NCP만 받고, 이미 저장된 `s3`·`gcs` 행은 목록에 그대로 나오는지 확인한다."""
    for provider in ("s3", "gcs"):
        r = await client.post("/connections", headers=_h(admin_token), json={
            "name": f"old-{provider}-{uuid.uuid4().hex[:8]}", "provider": provider,
            "config": {"bucket": "b"},
        })
        assert r.status_code == 422, (provider, r.text)

    org = await default_org_id()
    name = f"legacy-s3-{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as s:
        s.add(Connection(org_id=uuid.UUID(org), name=name, provider="s3", config={"bucket": "b"}))
        await s.commit()
    listed = (await client.get("/connections", headers=_h(admin_token))).json()
    assert any(c["name"] == name and c["provider"] == "s3" for c in listed)
