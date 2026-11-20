"""프로젝트가 쓰는 버킷을 저장소 연결의 허용 범위에서 빼는 수정의 거절 테스트.

허용 범위(연결 설정의 `bucket`과 `allowed_buckets` 목록의 합)에서 빠진 버킷을 쓰는 프로젝트는
등록·탐색·폴더 등록이 422 `storage_bucket_not_allowed`로 멈춘다. 그래서 연결 수정이 저장 전에
409 `allowed_bucket_in_use`로 거절하고, 막는 프로젝트 이름을 응답에 싣는다.

보장하는 것은 다음과 같다.
- 쓰는 버킷을 목록에서 빼거나 `bucket`을 바꿔 범위에서 빠지면 409와 프로젝트 이름이 온다.
- 쓰지 않는 버킷은 빼도 통과하고, 프로젝트 저장소를 다른 버킷으로 바꾼 뒤에는 같은 수정이 통과한다.
- 삭제한 프로젝트와 수정 전에도 범위 밖이던 버킷은 막지 않는다.
- 이름은 다섯 개까지 싣고 나머지는 수로 싣는다. 연결 삭제의 사용 중 거절도 같은 꼴이다.

저장소는 인프로세스 moto다. 두 번째 버킷을 만들고 모듈이 끝나면 지운다.
"""
import contextlib
import uuid
from datetime import UTC, datetime
from uuid import UUID

import boto3
import pytest
import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import Connection, Project
from api_gateway.services import platform_credentials as pc
from api_gateway.services.stacks import project_stack, project_storage
from httpx import AsyncClient
from tests import _storage
from tests._helpers import new_project

#: 연결 설정의 버킷이 아닌 두 번째 버킷.
_OTHER = "sample-inuse-other"


#: 연결의 처음 설정. 연결 수정은 설정 전체를 바꾸므로 고칠 키를 이 설정 위에 얹어 보낸다.
_BASE = {"endpoint": _storage.ENDPOINT, "bucket": _storage.BUCKET, "region": "kr-standard"}


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module", autouse=True)
def _other_bucket():
    """두 번째 버킷을 만들고 모듈이 끝나면 비우고 지운다.

    남기면 같은 프로세스의 저장소 목록 계약(`list_buckets() == ["test-bucket"]`)이 실패한다.
    """
    s3 = boto3.client("s3", endpoint_url=_storage.ENDPOINT, aws_access_key_id="testing",
                      aws_secret_access_key="testing", region_name="us-east-1")
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


async def _connection(client: AsyncClient, token: str) -> str:
    """moto를 가리키고 두 번째 버킷도 허용한 NCP 연결을 만들어 id를 돌려준다."""
    r = await client.post("/connections", headers=_h(token), json={
        "name": f"inuse-{uuid.uuid4().hex[:8]}", "provider": "ncp",
        "config": {"endpoint": _storage.ENDPOINT, "bucket": _storage.BUCKET,
                   "region": "kr-standard", "allowed_buckets": [_OTHER]},
        "access_key": "inuse-key", "secret_key": "inuse-secret",
    })
    assert r.status_code == 201, r.text
    assert r.json()["check"]["status"] == "ok", r.json()
    cid = r.json()["id"]
    return cid


async def _use(client: AsyncClient, token: str, pid: str, cid: str, bucket: str) -> None:
    """프로젝트 저장소를 화면과 같은 경로(`PUT storage-stack`)로 고른다."""
    r = await client.put(f"/projects/{pid}/storage-stack", headers=_h(token),
                         json={"storage_connection_id": cid, "bucket": bucket})
    assert r.status_code == 200, r.text


async def _name(pid: str) -> str:
    """프로젝트 이름을 DB에서 읽는다."""
    async with SessionLocal() as s:
        return (await s.get(Project, UUID(pid))).name


async def _config(cid: str) -> dict:
    """연결 설정을 DB에서 읽는다."""
    async with SessionLocal() as s:
        return dict((await s.get(Connection, UUID(cid))).config or {})


async def test_removing_a_used_bucket_from_the_list_is_refused(
    client: AsyncClient, admin_token: str,
):
    """쓰는 버킷을 목록에서 빼면 409와 프로젝트 이름이 오고 설정이 그대로인지 확인한다."""
    h = _h(admin_token)
    cid = await _connection(client, admin_token)
    pid = await new_project(client, admin_token, "inuse-list", storage=False)
    await _use(client, admin_token, pid, cid, _OTHER)

    r = await client.patch(f"/connections/{cid}", headers=h,
                           json={"config": {**_BASE, "allowed_buckets": []}})
    assert r.status_code == 409, r.text
    assert r.json() == {"detail": "allowed_bucket_in_use",
                        "projects": [await _name(pid)], "projects_more": 0}
    assert (await _config(cid))["allowed_buckets"] == [_OTHER], "거절했는데 설정이 바뀌었다"


async def test_removing_an_unused_bucket_passes(client: AsyncClient, admin_token: str):
    """프로젝트가 쓰지 않는 버킷은 목록에서 빼도 저장되는지 확인한다."""
    cid = await _connection(client, admin_token)
    pid = await new_project(client, admin_token, "inuse-unused", storage=False)
    await _use(client, admin_token, pid, cid, _storage.BUCKET)

    r = await client.patch(f"/connections/{cid}", headers=_h(admin_token),
                           json={"config": {**_BASE, "allowed_buckets": []}})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["allowed_buckets"] == []


async def test_changing_the_bucket_out_of_scope_is_refused(client: AsyncClient, admin_token: str):
    """연결의 `bucket`을 바꿔 쓰던 버킷이 범위에서 빠지면 409이고, 목록에 남기면 통과하는지 확인한다."""
    h = _h(admin_token)
    cid = await _connection(client, admin_token)
    pid = await new_project(client, admin_token, "inuse-bucket", storage=False)
    await _use(client, admin_token, pid, cid, _storage.BUCKET)

    r = await client.patch(f"/connections/{cid}", headers=h,
                           json={"config": {**_BASE, "bucket": _OTHER, "allowed_buckets": [_OTHER]}})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "allowed_bucket_in_use"
    assert r.json()["projects"] == [await _name(pid)]
    assert (await _config(cid))["bucket"] == _storage.BUCKET

    r = await client.patch(f"/connections/{cid}", headers=h, json={
        "config": {**_BASE, "bucket": _OTHER, "allowed_buckets": [_storage.BUCKET]}})
    assert r.status_code == 200, r.text


async def test_moving_the_project_first_lets_the_edit_pass(client: AsyncClient, admin_token: str):
    """프로젝트 저장소를 다른 버킷으로 바꾼 뒤에는 같은 수정이 통과하는지 확인한다."""
    h = _h(admin_token)
    cid = await _connection(client, admin_token)
    pid = await new_project(client, admin_token, "inuse-move", storage=False)
    await _use(client, admin_token, pid, cid, _OTHER)
    body = {"config": {**_BASE, "allowed_buckets": []}}
    r = await client.patch(f"/connections/{cid}", headers=h, json=body)
    assert r.status_code == 409, r.text

    await _use(client, admin_token, pid, cid, _storage.BUCKET)
    r = await client.patch(f"/connections/{cid}", headers=h, json=body)
    assert r.status_code == 200, r.text


async def test_deleted_projects_and_already_blocked_buckets_do_not_block(
    client: AsyncClient, admin_token: str,
):
    """삭제한 프로젝트와 수정 전에도 범위 밖이던 버킷은 수정을 막지 않는지 확인한다.

    범위 밖 버킷을 기록한 프로젝트는 이미 등록이 멈춰 있다. 그 프로젝트를 세면 연결의 다른 값도 고칠 수 없다.
    """
    h = _h(admin_token)
    cid = await _connection(client, admin_token)
    gone = await new_project(client, admin_token, "inuse-gone", storage=False)
    await _use(client, admin_token, gone, cid, _OTHER)
    stale = await new_project(client, admin_token, "inuse-stale", storage=False)
    async with SessionLocal() as s:
        (await s.get(Project, UUID(gone))).deleted_at = datetime.now(UTC)
        p = await s.get(Project, UUID(stale))
        p.settings = pc.with_storage_target(p.settings, pc.StorageTarget(cid, "never-allowed"))
        await s.commit()

    r = await client.patch(f"/connections/{cid}", headers=h,
                           json={"config": {**_BASE, "allowed_buckets": []}})
    assert r.status_code == 200, r.text


async def test_names_are_capped_and_delete_uses_the_same_shape(
    client: AsyncClient, admin_token: str,
):
    """이름은 정렬해 다섯 개까지 싣고 나머지는 수로 싣는지, 연결 삭제의 거절도 같은 꼴인지 확인한다."""
    h = _h(admin_token)
    cid = await _connection(client, admin_token)
    pids = [await new_project(client, admin_token, f"inuse-many{i}", storage=False)
            for i in range(7)]
    for pid in pids:
        await _use(client, admin_token, pid, cid, _OTHER)
    names = sorted([await _name(pid) for pid in pids])

    r = await client.patch(f"/connections/{cid}", headers=h,
                           json={"config": {**_BASE, "allowed_buckets": []}})
    assert r.status_code == 409, r.text
    assert r.json() == {"detail": "allowed_bucket_in_use",
                        "projects": names[:5], "projects_more": 2}

    r = await client.delete(f"/connections/{cid}", headers=h)
    assert r.status_code == 409, r.text
    assert r.json() == {"detail": "connection_in_use",
                        "projects": names[:5], "projects_more": 2}
