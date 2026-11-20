"""저장소 연결의 허용 버킷 범위 테스트.

저장소 키는 보통 계정의 모든 버킷에 닿는다. 프로젝트 관리자가 아무 버킷이나 프로젝트 저장소로 고르면
같은 계정의 다른 버킷 객체를 탐색하고 영상으로 등록해 읽는다. 그래서 연결이 허용한 버킷
(연결 설정의 `bucket`과 `allowed_buckets` 목록의 합)만 고르고 쓴다.

보장하는 것은 다음과 같다.
- 선택지는 허용 범위 안의 버킷만 싣고, 저장은 범위 밖 버킷을 점검 전에 422로 막는다.
- 이미 범위 밖 버킷을 기록한 프로젝트는 탐색·등록·폴더 등록·영상 등록이 422로 막힌다.
- 이행(마이그레이션 0108의 `fill`)은 그 연결을 고른 프로젝트들의 버킷을 목록에 채워 같은 프로젝트를
  다시 동작하게 하고, 이미 목록이 있는 연결은 건드리지 않는다.
- 목록은 버킷 이름 목록만 받고, 바꾸면 연결 수정 감사 기록에 전후 값이 남는다.

저장소는 인프로세스 moto다. 허용 범위 밖 버킷을 하나 더 만들고 모듈이 끝나면 지운다.
"""
import contextlib
import importlib.util
import uuid
from pathlib import Path
from uuid import UUID

import boto3
import pytest
import pytest_asyncio
from api_gateway.database import SessionLocal, engine
from api_gateway.models import Connection, DomainEvent, Project
from api_gateway.services import platform_credentials as pc
from api_gateway.services.stacks import project_stack, project_storage
from httpx import AsyncClient
from sqlalchemy import select
from tests import _storage
from tests._helpers import new_project

#: 연결 설정의 버킷이 아닌 두 번째 버킷. 계정 키는 닿지만 허용 범위에는 처음에 없다.
_OTHER = "sample-allow-other"
#: 이행 함수를 담은 마이그레이션 파일.
_MIGRATION = (Path(__file__).resolve().parents[2] / "alembic/versions"
              / "0108_connection_allowed_buckets.py")


#: 연결의 처음 설정. 연결 수정은 설정 전체를 바꾸므로 고칠 키를 이 설정 위에 얹어 보낸다.
_BASE = {"endpoint": _storage.ENDPOINT, "bucket": _storage.BUCKET, "region": "kr-standard"}


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


def _s3():
    """moto에 직접 붙는 boto3 클라이언트를 만든다."""
    return boto3.client("s3", endpoint_url=_storage.ENDPOINT, aws_access_key_id="testing",
                        aws_secret_access_key="testing", region_name="us-east-1")


@pytest.fixture(scope="module", autouse=True)
def _other_bucket():
    """두 번째 버킷을 만들고 모듈이 끝나면 비우고 지운다.

    남기면 같은 프로세스의 저장소 목록 계약(`list_buckets() == ["test-bucket"]`)이 실패한다.
    """
    s3 = _s3()
    with contextlib.suppress(Exception):
        s3.create_bucket(Bucket=_OTHER)
    yield
    with contextlib.suppress(Exception):
        for o in s3.list_objects_v2(Bucket=_OTHER).get("Contents") or []:
            s3.delete_object(Bucket=_OTHER, Key=o["Key"])
        s3.delete_bucket(Bucket=_OTHER)


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _reset():
    """테스트마다 연결 메모리와 점검 캐시를 처음으로 돌린다."""
    await _storage.reset_memory()
    project_storage._cache.clear()
    project_stack._cache.clear()
    yield
    await _storage.reset_memory()
    project_storage._cache.clear()
    project_stack._cache.clear()


def _fill():
    """마이그레이션 0108의 이행 함수를 불러온다."""
    spec = importlib.util.spec_from_file_location("migration_0108_test", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.fill


async def _run_fill(conn_ids: list[str]) -> list[str]:
    """이 테스트의 연결에만 이행을 돌리고 커밋한다."""
    fill = _fill()
    async with engine.begin() as conn:
        return await conn.run_sync(lambda c: fill(c, conn_ids))


async def _connection(client: AsyncClient, token: str, **extra) -> str:
    """moto를 가리키는 NCP 연결을 만들고 저장 점검이 정상인지 확인해 id를 돌려준다.

    연결 설정의 버킷은 테스트 버킷이다. `extra`는 설정에 더할 키다.
    """
    r = await client.post("/connections", headers=_h(token), json={
        "name": f"allow-{uuid.uuid4().hex[:8]}", "provider": "ncp",
        "config": {"endpoint": _storage.ENDPOINT, "bucket": _storage.BUCKET,
                   "region": "kr-standard", **extra},
        "access_key": "allow-key", "secret_key": "allow-secret",
    })
    assert r.status_code == 201, r.text
    assert r.json()["check"]["status"] == "ok", r.json()
    cid = r.json()["id"]
    return cid


async def _pin(pid: str, cid: str, bucket: str) -> None:
    """프로젝트 설정에 저장소 대상을 바로 쓴다. 허용 범위가 생기기 전에 기록된 값을 흉내 낸다."""
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(pid))
        p.settings = pc.with_storage_target(p.settings, pc.StorageTarget(cid, bucket))
        await s.commit()
        await pc.refresh(s)


async def _config(cid: str) -> dict:
    """연결 설정을 DB에서 읽는다."""
    async with SessionLocal() as s:
        return dict((await s.get(Connection, UUID(cid))).config or {})


# 선택지와 저장


async def test_choices_list_only_allowed_buckets(client: AsyncClient, admin_token: str):
    """키가 닿는 버킷 중 허용 범위 안의 것만 선택지에 싣고, 목록을 넓히면 함께 넓어지는지 확인한다."""
    h = _h(admin_token)
    cid = await _connection(client, admin_token)
    pid = await new_project(client, admin_token, "allow-choices", storage=False)
    c = (await client.get(f"/projects/{pid}/storage-choices", headers=h,
                          params={"connection_id": cid})).json()
    assert c["buckets"] == [_storage.BUCKET], c
    option = next(o for o in c["connections"] if o["id"] == cid)
    assert option["allowed_buckets"] == [_storage.BUCKET]

    r = await client.patch(f"/connections/{cid}", headers=h,
                           json={"config": {**_BASE, "allowed_buckets": [_OTHER]}})
    assert r.status_code == 200, r.text
    c = (await client.get(f"/projects/{pid}/storage-choices", headers=h,
                          params={"connection_id": cid})).json()
    assert sorted(c["buckets"]) == sorted([_storage.BUCKET, _OTHER]), c
    option = next(o for o in c["connections"] if o["id"] == cid)
    assert option["allowed_buckets"] == sorted([_storage.BUCKET, _OTHER])


async def test_save_refuses_a_bucket_outside_the_allowlist(client: AsyncClient, admin_token: str):
    """허용 범위 밖 버킷은 점검 전에 422로 저장하지 않고, 목록에 넣으면 같은 저장이 통과하는지 확인한다."""
    h = _h(admin_token)
    cid = await _connection(client, admin_token)
    pid = await new_project(client, admin_token, "allow-save", storage=False)
    body = {"storage_connection_id": cid, "bucket": _OTHER}
    r = await client.put(f"/projects/{pid}/storage-stack", headers=h, json=body)
    assert r.status_code == 422 and r.json()["detail"] == "storage_bucket_not_allowed", r.text
    assert not _s3().list_objects_v2(Bucket=_OTHER).get("Contents"), "범위 밖 버킷에 점검 객체를 썼다"

    r = await client.patch(f"/connections/{cid}", headers=h,
                           json={"config": {**_BASE, "allowed_buckets": [_OTHER]}})
    assert r.status_code == 200, r.text
    r = await client.put(f"/projects/{pid}/storage-stack", headers=h, json=body)
    assert r.status_code == 200, r.text


# 이미 기록한 프로젝트와 이행


async def test_routes_block_a_recorded_bucket_outside_the_allowlist_until_filled(
    client: AsyncClient, admin_token: str,
):
    """범위 밖 버킷을 이미 기록한 프로젝트는 등록·폴더 등록·영상 등록이 422이고, 이행 뒤에는 그대로 동작하는지 확인한다.

    허용 범위가 생기기 전에 기록한 버킷은 목록에 없다. 이행이 그 버킷을 목록에 채우지 않으면 마이그레이션 직후 그 프로젝트가 멈춘다.
    """
    h = _h(admin_token)
    cid = await _connection(client, admin_token)
    pid = await new_project(client, admin_token, "allow-legacy", analysis_preset="ncp-shots",
                            storage=False)
    await _pin(pid, cid, _OTHER)
    _s3().put_object(Bucket=_OTHER, Key="raw/legacy.mp4", Body=b"x")

    blocked = [
        await client.post(f"/projects/{pid}/storage/register", headers=h, json={
            "keys": ["raw/legacy.mp4"], "copyright_policy": "pre_cleared"}),
        await client.post(f"/projects/{pid}/storage/register-prefix", headers=h, json={
            "prefix": "raw/", "copyright_policy": "pre_cleared",
            "upload_batch_id": str(uuid.uuid4())}),
        await client.post(f"/projects/{pid}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"}),
    ]
    for r in blocked:
        assert r.status_code == 422, (r.request.url, r.text)
        assert r.json()["detail"] == "storage_bucket_not_allowed", (r.request.url, r.text)

    assert await _run_fill([cid]) == [cid]
    assert (await _config(cid))["allowed_buckets"] == sorted([_storage.BUCKET, _OTHER])
    r = await client.post(f"/projects/{pid}/storage/register", headers=h, json={
        "keys": ["raw/legacy.mp4"], "copyright_policy": "pre_cleared"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/projects/{pid}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 201, r.text


async def test_fill_keeps_set_lists_and_skips_connections_without_buckets(
    client: AsyncClient, admin_token: str,
):
    """이행이 이미 목록이 있는 연결과 채울 버킷이 없는 연결을 건드리지 않고, 채운 연결에 감사 기록을 남기는지 확인한다.

    다시 돌려도 아무것도 바꾸지 않는다. 사람이 정한 목록을 덮으면 좁혀 둔 범위가 다시 넓어진다.
    """
    kept = await _connection(client, admin_token, allowed_buckets=[])
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": f"allow-empty-{uuid.uuid4().hex[:8]}", "provider": "ncp", "config": {}})
    empty = r.json()["id"]
    filled = await _connection(client, admin_token)

    assert await _run_fill([kept, empty, filled]) == [filled]
    assert (await _config(kept))["allowed_buckets"] == []
    assert "allowed_buckets" not in await _config(empty)
    assert (await _config(filled))["allowed_buckets"] == [_storage.BUCKET]
    assert await _run_fill([kept, empty, filled]) == [], "다시 돌렸더니 또 바꿨다"

    async with SessionLocal() as s:
        events = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "connection.updated",
            DomainEvent.target_id == UUID(filled)))).all()
    assert len(events) == 1
    assert events[0].payload["bucket_changes"] == {
        "allowed_buckets": {"before": None, "after": [_storage.BUCKET]}}


# 목록 편집


@pytest.mark.parametrize("value", ["b1", [1], ["A_B"], ["ok-bucket", "x"]])
async def test_allowed_buckets_must_be_bucket_names(
    client: AsyncClient, admin_token: str, value: object,
):
    """허용 버킷 목록이 버킷 이름 문자열의 목록이 아니면 422인지 확인한다."""
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": f"allow-bad-{uuid.uuid4().hex[:8]}", "provider": "ncp",
        "config": {"bucket": "b-ok", "allowed_buckets": value}})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "connection_allowed_buckets_invalid"


async def test_allowed_buckets_are_cleaned_and_audited(client: AsyncClient, admin_token: str):
    """목록을 정리해 저장하고(공백·중복·빈 항목 제거, 정렬) 바꾸면 감사 기록에 전후 값이 남는지 확인한다."""
    cid = await _connection(client, admin_token)
    r = await client.patch(f"/connections/{cid}", headers=_h(admin_token), json={
        "config": {**_BASE, "allowed_buckets": [" zeta-b ", "alpha-b", "", "zeta-b"]}})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["allowed_buckets"] == ["alpha-b", "zeta-b"]
    async with SessionLocal() as s:
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "connection.updated",
            DomainEvent.target_id == UUID(cid)))).one()
    assert ev.payload["config_keys_changed"] == ["allowed_buckets"]
    assert ev.payload["bucket_changes"] == {
        "allowed_buckets": {"before": None, "after": ["alpha-b", "zeta-b"]}}
    assert "address_changes" not in ev.payload


async def test_only_super_admin_edits_the_allowlist(client: AsyncClient, admin_token: str):
    """프로젝트 관리자는 연결을 고칠 수 없어 허용 범위를 넓히지 못하는지 확인한다."""
    from tests._helpers import add_member

    cid = await _connection(client, admin_token)
    pid = await new_project(client, admin_token, "allow-perm")
    _, padmin = await add_member(client, admin_token, pid, "project_admin")
    r = await client.patch(f"/connections/{cid}", headers=_h(padmin),
                           json={"config": {**_BASE, "allowed_buckets": [_OTHER]}})
    assert r.status_code == 403, r.text
