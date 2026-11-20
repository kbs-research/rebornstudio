"""테스트 저장소 연결. moto를 가리키는 NCP 저장소 연결을 DB에 두고 테스트 프로젝트가 고르게 한다.

앱은 저장소 자격증명을 등록한 연결에서만 읽는다(`platform_credentials`). 그래서 테스트도 환경
변수가 아니라 연결을 등록하고, 저장소를 쓰는 프로젝트에 그 연결과 버킷을 저장소 대상으로 기록한다.

조직은 기본 조직 한 행뿐이므로 연결도 그 조직에 하나만 둔다. 세션 픽스처가 만들고(`connection_id`),
객체를 직접 올리는 헬퍼(`store`·`put_bytes`)와 프로젝트 저장소 대상(`connection_for`)이 같은 연결을 쓴다.
"""
from __future__ import annotations

import json
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Connection
from api_gateway.services import crypto
from api_gateway.services import platform_credentials as pc
from api_gateway.services.org import organization as org_service
from sqlalchemy import select, text
from tests import _moto_bootstrap as moto

#: 테스트 저장소 연결 이름.
NAME = "test-moto-ncp"
BUCKET = moto.BUCKET
#: moto 서버 주소. 테스트가 연결을 더 만들 때 쓴다.
ENDPOINT = moto.ENDPOINT

_id: str | None = None


async def _upsert(s, org_id: UUID) -> str:
    """조직에 moto를 가리키는 NCP 저장소 연결을 찾거나 만들고 id를 돌려준다.

    moto 포트는 실행마다 바뀌므로 주소와 키를 매번 덮어쓴다. 점검 결과는 정상으로 둔다.
    """
    conn = await s.scalar(select(Connection).where(
        Connection.org_id == org_id, Connection.name == NAME))
    if conn is None:
        conn = Connection(org_id=org_id, name=NAME, provider=pc.STORAGE_PROVIDER)
        s.add(conn)
    conn.config = {"endpoint": moto.ENDPOINT, "bucket": BUCKET, "region": "kr-standard"}
    conn.secret_ciphertext = crypto.encrypt(f"{moto.ACCESS_KEY}\n{moto.SECRET_KEY}")
    conn.last_status = "ok"
    await s.commit()
    return str(conn.id)


async def ensure() -> str:
    """기본 조직의 테스트 저장소 연결을 찾거나 만들고 id를 돌려준다. 프로세스 메모리의 연결 값도 다시 읽는다."""
    global _id
    async with SessionLocal() as s:
        _id = await _upsert(s, await org_service.default_org_id(s))
        await pc.refresh(s)
    return _id


async def connection_for(project_id: str | UUID) -> str:
    """프로젝트가 고를 테스트 저장소 연결 id를 돌려준다. 없으면 만든다.

    저장 라우트로 저장소 대상을 저장하는 테스트가 쓴다. 프로젝트가 있는지도 확인한다.
    """
    from api_gateway.models import Project

    async with SessionLocal() as s:
        org_id = await s.scalar(
            select(Project.org_id).where(Project.project_id == UUID(str(project_id))))
        assert org_id is not None, f"프로젝트가 없다: {project_id}"
        cid = await _upsert(s, org_id)
        if not pc.storage_connection_loaded(cid):
            await pc.refresh(s)
    return cid


def connection_id() -> str:
    """테스트 저장소 연결 id를 돌려준다. 세션 픽스처가 먼저 만든다."""
    assert _id is not None, "테스트 저장소 연결이 아직 없다(conftest의 세션 픽스처)"
    return _id


def target() -> pc.StorageTarget:
    """테스트 저장소 연결과 버킷을 저장소 선택 모양으로 돌려준다."""
    return pc.StorageTarget(connection_id(), BUCKET)


def store() -> pc.StorageCredentials:
    """테스트 저장소 값을 돌려준다. 저장소 함수에 `store=`로 넘길 때 쓴다."""
    return pc.storage_for(target())


async def pin_project(project_id: str | UUID, *, bucket: str | None = None) -> None:
    """프로젝트 설정에 테스트 저장소 연결과 버킷을 저장소 대상으로 기록한다.

    저장 라우트의 점검(쓰기·서명한 업로드·CORS)을 거치지 않고 DB에 바로 쓴다. 저장 규칙을 확인하는
    테스트는 라우트를 직접 부른다.
    """
    block = {pc.STACK_STORAGE_CONNECTION: await connection_for(project_id),
             pc.STACK_BUCKET: bucket or BUCKET}
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE projects SET settings = coalesce(settings, '{}'::jsonb) || "
            "jsonb_build_object('stack', coalesce(settings->'stack', '{}'::jsonb) || "
            "CAST(:b AS jsonb)) WHERE project_id = :pid"
        ), {"b": json.dumps(block), "pid": UUID(str(project_id))})
        await s.commit()
        await pc.refresh(s)


def put_bytes(key: str, body: bytes, content_type: str = "application/octet-stream") -> str:
    """테스트 저장소 버킷에 객체를 바로 올리고 버킷 이름을 돌려준다. 시드 객체를 만들 때 쓴다."""
    from api_gateway.services.media import storage

    return storage.put_bytes(key, body, content_type, store=store())


async def reset_memory() -> None:
    """프로세스 메모리의 연결 값을 비우고 DB에서 다시 읽는다. 테스트가 상태를 처음으로 돌릴 때 쓴다."""
    pc.reset()
    async with SessionLocal() as s:
        await pc.refresh(s)
