"""키 없는 Bedrock 연결을 저장하지 않는지 보는 테스트.

키 없는 Bedrock 연결은 boto3 기본 자격증명 체인으로 넘어간다. 앱 호스트에 AWS 환경변수나 인스턴스 역할이
있으면 그 서버 권한이 연결 설정 하나로 쓰인다. 보장하는 것은 다음과 같다.

1. 생성과 수정은 수정 뒤에 두 키가 모두 남지 않으면 422 `bedrock_keys_required`이고 행을 만들거나 바꾸지 않는다.
2. 두 키가 있으면 저장하고 같은 요청에서 점검해 결과를 행에 남긴다.
3. 이미 저장된 키 없는 연결은 점검이 AWS를 부르지 않고 실패로 표시한다. 데이터는 바꾸지 않는다.
4. 다른 서비스는 키 없이 먼저 등록하는 흐름을 그대로 둔다.
"""
import uuid

import boto3
import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Connection
from api_gateway.services.stacks import connections as conn_service
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from tests._helpers import default_org_id

#: Bedrock 연결 설정.
_CONFIG = {"region": "us-east-1", "model": "m"}


def _h(token: str) -> dict[str, str]:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def no_aws(monkeypatch) -> list[dict]:
    """`boto3.client` 호출을 기록하고 실패시킨다. 키 없는 경로가 AWS에 닿았는지 본다."""
    calls: list[dict] = []

    def _refuse(*args, **kwargs):
        """호출을 기록하고 멈춘다."""
        calls.append(kwargs)
        raise AssertionError("키 없는 Bedrock 경로가 boto3를 불렀다")

    monkeypatch.setattr(boto3, "client", _refuse)
    return calls


@pytest.fixture
def fake_bedrock(monkeypatch) -> None:
    """Bedrock 모델 목록 조회를 가짜 클라이언트로 바꾼다. 두 키가 있는 저장이 점검까지 가는지 본다."""

    class _Client:
        """모델 하나를 돌려주는 가짜 Bedrock 클라이언트."""

        def list_foundation_models(self):
            """설정의 모델이 들어 있는 목록을 돌려준다."""
            return {"modelSummaries": [{"modelId": "m"}]}

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Client())


async def _create(client: AsyncClient, token: str, name: str, **keys):
    """Bedrock 연결 생성 요청을 보낸다."""
    return await client.post("/connections", headers=_h(token), json={
        "name": name, "provider": "bedrock", "config": _CONFIG, **keys})


async def _count(name: str) -> int:
    """이름이 같은 연결 행 수를 센다."""
    async with SessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(Connection)
                              .where(Connection.name == name))


async def test_create_requires_both_keys(
    client: AsyncClient, admin_token: str, no_aws: list[dict],
):
    """두 키가 모두 없으면 422이고 행이 생기지 않으며 AWS를 부르지 않는지 확인한다."""
    for keys in ({}, {"access_key": "AKIA"}, {"secret_key": "s"},
                 {"access_key": "AKIA", "secret_key": ""}, {"access_key": " ", "secret_key": "s"}):
        name = f"bedrock-{uuid.uuid4().hex[:8]}"
        r = await _create(client, admin_token, name, **keys)
        assert r.status_code == 422 and r.json()["detail"] == "bedrock_keys_required", (keys, r.text)
        assert await _count(name) == 0
    assert no_aws == []


@pytest.mark.real_probe
async def test_create_with_both_keys_saves_and_records_the_check(
    client: AsyncClient, admin_token: str, fake_bedrock: None,
):
    """두 키가 있으면 저장하고 같은 요청의 점검 결과를 응답과 행에 남기는지 확인한다."""
    r = await _create(client, admin_token, f"bedrock-{uuid.uuid4().hex[:8]}",
                      access_key="AKIA", secret_key="s")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_secret"] is True and body["check"]["status"] == "ok", body
    async with SessionLocal() as s:
        row = await s.get(Connection, uuid.UUID(body["id"]))
        assert row.last_status == "ok" and row.last_checked_at is not None


async def test_update_cannot_leave_bedrock_without_keys(
    client: AsyncClient, admin_token: str, fake_bedrock: None,
):
    """수정이 키를 지우거나 한쪽만 보내 Bedrock 연결에 두 키가 남지 않으면 422인지 확인한다."""
    r = await _create(client, admin_token, f"bedrock-{uuid.uuid4().hex[:8]}",
                      access_key="AKIA", secret_key="s")
    url = f"/connections/{r.json()['id']}"
    for patch in ({"clear_secret": True}, {"access_key": "AKIA2"}, {"secret_key": "s2"},
                  {"clear_secret": True, "access_key": "AKIA", "secret_key": "s"}):
        r = await client.patch(url, headers=_h(admin_token), json=patch)
        assert r.status_code == 422 and r.json()["detail"] == "bedrock_keys_required", (patch, r.text)

    # 저장된 키가 있으면 키를 보내지 않고 다른 값만 고쳐도 된다. 리전이 같아 키를 다시 받지 않는다.
    r = await client.patch(url, headers=_h(admin_token), json={"config": {**_CONFIG, "model": "m2"}})
    assert r.status_code == 200 and r.json()["has_secret"] is True, r.text


@pytest.mark.real_probe
async def test_existing_keyless_connection_fails_its_check_without_calling_aws(
    client: AsyncClient, admin_token: str, no_aws: list[dict],
):
    """이미 저장된 키 없는 연결은 점검이 AWS를 부르지 않고 실패이고, 키 없이 고칠 수 없는지 확인한다."""
    async with SessionLocal() as s:
        conn = Connection(org_id=uuid.UUID(await default_org_id()),
                          name=f"bedrock-old-{uuid.uuid4().hex[:8]}",
                          provider="bedrock", config=_CONFIG)
        s.add(conn)
        await s.commit()
        cid = conn.id
    try:
        async with SessionLocal() as s:
            row = await s.get(Connection, cid)
            result = await conn_service.check_saved(s, row)
        assert result.status == "fail", result
        assert "키" in result.detail

        r = await client.patch(f"/connections/{cid}", headers=_h(admin_token),
                               json={"config": {**_CONFIG, "model": "m2"}})
        assert r.status_code == 422 and r.json()["detail"] == "bedrock_keys_required", r.text
        async with SessionLocal() as s:
            row = await s.get(Connection, cid)
            assert row.config == _CONFIG and row.secret_ciphertext is None
        assert no_aws == []
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Connection).where(Connection.id == cid))
            await s.commit()


async def test_other_providers_keep_saving_without_keys(client: AsyncClient, admin_token: str):
    """키 검사가 Bedrock에만 걸리는지 확인한다. 사내 모델 연결은 키 없이 만들 수 있다."""
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": f"llm-{uuid.uuid4().hex[:8]}", "provider": "local-llm",
        "config": {"base_url": "https://llm.example.com/v1", "model": "m"},
    })
    assert r.status_code == 201, r.text
