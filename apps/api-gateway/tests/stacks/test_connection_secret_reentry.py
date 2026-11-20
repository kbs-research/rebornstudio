"""연결 주소를 바꾸는 수정이 시크릿을 다시 받는지 보는 테스트.

수정은 시크릿을 보내지 않으면 저장된 값을 둔다. 주소만 바꾸면 저장된 키가 점검 한 번에 새 주소로
나가므로, 요청이 나가는 곳을 정하는 키를 바꾸는 수정은 같은 요청에 시크릿을 받는다.
판정 표는 `packages/reborn-contracts/connection-address.json`이고 `tests/contract`가 함수 단위로 돌린다.
이 파일은 수정 라우트가 그 규칙을 지키는지 본다.
"""
import uuid

import pytest
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import Connection, DomainEvent
from api_gateway.services import crypto
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import default_org_id


@pytest.fixture(autouse=True)
def _empty_allowlist(monkeypatch):
    """테스트 공통 허용 목록(moto의 루프백)을 비워 실제 운영 기본값으로 주소를 판정한다."""
    monkeypatch.setattr(settings, "connection_host_allowlist", "")


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _create(client: AsyncClient, token: str, provider: str, config: dict,
                  secret: dict | None = None) -> str:
    """시크릿이 있는 연결을 만들고 연결 id를 돌려준다.

    `s3`는 새로 만들 수 없는 연결 종류라 이미 저장된 행처럼 DB에 직접 넣는다.
    """
    if provider == "s3":
        async with SessionLocal() as s:
            conn = Connection(
                org_id=uuid.UUID(await default_org_id()),
                name=f"reentry-{uuid.uuid4().hex[:8]}", provider=provider, config=config,
                secret_ciphertext=crypto.encrypt("AK-OLD\nSK-OLD"))
            s.add(conn)
            await s.commit()
            return str(conn.id)
    r = await client.post("/connections", headers=_h(token), json={
        "name": f"reentry-{uuid.uuid4().hex[:8]}", "provider": provider, "config": config,
        **(secret if secret is not None else {"access_key": "AK-OLD", "secret_key": "SK-OLD"}),
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _secret_of(cid: str) -> str | None:
    """연결에 저장된 시크릿을 복호해 돌려준다. 없으면 `None`이다."""
    async with SessionLocal() as s:
        conn = await s.get(Connection, uuid.UUID(cid))
        return crypto.decrypt(conn.secret_ciphertext) if conn.secret_ciphertext else None


#: 연결 종류별 처음 설정과 요청이 나가는 곳을 바꾼 설정. 수정은 설정 전체를 바꾼다.
_MOVES = [
    ("ncp", {"endpoint": "https://kr.object.ncloudstorage.com", "bucket": "sample-bucket",
             "region": "kr-standard"},
     {"endpoint": "https://other.example.com", "bucket": "sample-bucket",
      "region": "kr-standard"}),
    ("ncp", {"endpoint": "https://kr.object.ncloudstorage.com", "bucket": "sample-bucket",
             "region": "kr-standard"},
     {"endpoint": "https://kr.object.ncloudstorage.com", "bucket": "sample-bucket",
      "region": "us-standard"}),
    ("s3", {"bucket": "sample-bucket", "region": "ap-northeast-2"},
     {"bucket": "sample-bucket", "region": "us-east-1"}),
    ("s3", {"endpoint": "https://s3.example.com", "bucket": "sample-bucket"},
     {"bucket": "sample-bucket"}),
    ("maiu", {"base_url": "https://mi.apigw.ntruss.com", "workspace_name": "w"},
     {"base_url": "https://mi.other.example.com", "workspace_name": "w"}),
    ("openai", {"model": "m"}, {"model": "m", "base_url": "https://proxy.example.com/v1"}),
    ("anthropic", {"model": "m"}, {"model": "m", "base_url": "https://proxy.example.com"}),
    ("local-llm", {"base_url": "http://10.0.0.5:8000/v1", "model": "m"},
     {"base_url": "http://10.0.0.6:8000/v1", "model": "m"}),
    ("bedrock", {"region": "us-east-1", "model": "m"}, {"region": "us-west-2", "model": "m"}),
]


@pytest.mark.parametrize(("provider", "config", "moved"), _MOVES)
async def test_address_change_without_secret_is_rejected(
    client: AsyncClient, admin_token: str, provider: str, config: dict, moved: dict,
):
    """주소만 바꾸면 422이고 설정과 저장된 시크릿이 그대로인지 확인한다."""
    cid = await _create(client, admin_token, provider, config)
    r = await client.patch(f"/connections/{cid}", headers=_h(admin_token),
                           json={"config": moved})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "secret_required_for_address_change"
    listed = (await client.get("/connections", headers=_h(admin_token))).json()
    assert next(c for c in listed if c["id"] == cid)["config"] == config
    assert await _secret_of(cid) == "AK-OLD\nSK-OLD"


@pytest.mark.parametrize(("provider", "config", "moved"), _MOVES)
async def test_address_change_with_secret_is_accepted(
    client: AsyncClient, admin_token: str, provider: str, config: dict, moved: dict,
):
    """주소와 시크릿을 같은 요청에 보내면 둘 다 바뀌는지 확인한다."""
    cid = await _create(client, admin_token, provider, config)
    r = await client.patch(f"/connections/{cid}", headers=_h(admin_token),
                           json={"config": moved, "access_key": "AK-NEW", "secret_key": "SK-NEW"})
    assert r.status_code == 200, r.text
    assert r.json()["config"] == moved
    assert await _secret_of(cid) == "AK-NEW\nSK-NEW"


async def test_resending_the_same_address_and_other_keys_need_no_secret(
    client: AsyncClient, admin_token: str,
):
    """같은 주소를 다시 보내거나 주소가 아닌 값만 바꾸면 시크릿 없이 통과하는지 확인한다.

    편집 창은 모든 입력란을 함께 보낸다. 같은 값까지 바꾼 것으로 세면 저장소 하나를 고칠 때도
    키를 다시 넣어야 한다. 끝 슬래시와 앞뒤 공백만 다른 주소도 같은 주소다.
    """
    config = {"endpoint": "https://kr.object.ncloudstorage.com", "bucket": "sample-bucket",
              "region": "kr-standard"}
    cid = await _create(client, admin_token, "ncp", config)
    same = await client.patch(f"/connections/{cid}", headers=_h(admin_token), json={"config": {
        **config, "endpoint": "https://kr.object.ncloudstorage.com/ ",
        "bucket": "sample-bucket-2"}})
    assert same.status_code == 200, same.text
    assert same.json()["config"]["bucket"] == "sample-bucket-2"
    assert await _secret_of(cid) == "AK-OLD\nSK-OLD"

    mcid = await _create(client, admin_token, "bedrock", {"region": "us-east-1", "model": "a"})
    model = await client.patch(f"/connections/{mcid}", headers=_h(admin_token),
                               json={"config": {"region": "us-east-1", "model": "b"}})
    assert model.status_code == 200, model.text


async def test_clearing_the_secret_or_having_none_lets_the_address_move(
    client: AsyncClient, admin_token: str,
):
    """시크릿을 지우면서 주소를 바꾸거나 시크릿이 없는 연결의 주소를 바꾸면 통과하는지 확인한다.

    두 경우 모두 새 주소로 나갈 저장된 시크릿이 없다.
    """
    cid = await _create(client, admin_token, "maiu",
                        {"base_url": "https://mi.apigw.ntruss.com", "workspace_name": "w"})
    r = await client.patch(f"/connections/{cid}", headers=_h(admin_token), json={
        "config": {"base_url": "https://mi.other.example.com", "workspace_name": "w"},
        "clear_secret": True})
    assert r.status_code == 200, r.text
    assert r.json()["has_secret"] is False

    bare = await _create(client, admin_token, "ncp", {"bucket": "sample-bucket"}, secret={})
    r = await client.patch(f"/connections/{bare}", headers=_h(admin_token), json={
        "config": {"endpoint": "https://other.example.com", "bucket": "sample-bucket"}})
    assert r.status_code == 200, r.text


async def test_blocked_address_is_reported_before_the_secret_rule(
    client: AsyncClient, admin_token: str,
):
    """막힌 주소는 시크릿 재입력보다 먼저 막힌 주소로 거절하는지 확인한다.

    키를 넣어도 풀리지 않는 사유를 먼저 알려야 다시 입력한 키가 헛되지 않는다.
    """
    cid = await _create(client, admin_token, "ncp",
                        {"endpoint": "https://s3.example.com", "bucket": "sample-bucket"})
    r = await client.patch(f"/connections/{cid}", headers=_h(admin_token), json={
        "config": {"endpoint": "http://169.254.169.254", "bucket": "sample-bucket"}})
    assert r.status_code == 422 and r.json()["detail"] == "connection_url_blocked", r.text


async def test_rejected_update_leaves_no_audit_record(client: AsyncClient, admin_token: str):
    """거절된 수정은 감사 기록을 남기지 않고, 받아들인 수정은 리전 전후 값을 남기는지 확인한다."""
    cid = await _create(client, admin_token, "bedrock", {"region": "us-east-1", "model": "m"})
    url = f"/connections/{cid}"
    bad = await client.patch(url, headers=_h(admin_token),
                             json={"config": {"region": "us-west-2", "model": "m"}})
    assert bad.status_code == 422
    ok = await client.patch(url, headers=_h(admin_token), json={
        "config": {"region": "us-west-2", "model": "m"},
        "access_key": "AK2", "secret_key": "SK2"})
    assert ok.status_code == 200, ok.text
    async with SessionLocal() as s:
        events = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "connection.updated",
            DomainEvent.target_id == uuid.UUID(cid)))).all()
    assert len(events) == 1
    assert events[0].payload["address_changes"] == {
        "region": {"before": "us-east-1", "after": "us-west-2"}}
    assert events[0].payload["secret_replaced"] is True
