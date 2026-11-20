"""연결 주소가 서버 내부와 클라우드 메타데이터를 가리키지 못하게 막는지 보는 테스트.

연결 점검, 품질검수 호출, 저장소 접속은 서버에서 나간다. 주소에 메타데이터 서비스
(169.254.169.254)나 같은 서버 안의 서비스(postgres·redis·api)를 받으면 서버가 그 요청을 대신
보낸다. 사내 품질검수 모델은 사설 대역에 있으므로 사설 대역은 받는다.
Bedrock 연결에 키가 없으면 boto3가 기본 자격증명 체인으로 넘어가 인스턴스 메타데이터를 조회한다.
"""
import uuid

import pytest
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import Connection
from api_gateway.services.caption_qc import adapters
from api_gateway.services.stacks import connections as conn_service
from httpx import AsyncClient
from tests._helpers import default_org_id


@pytest.fixture(autouse=True)
def _empty_allowlist(monkeypatch):
    """테스트 공통 허용 목록(moto의 루프백)을 비워 실제 운영 기본값으로 판정한다."""
    monkeypatch.setattr(settings, "connection_host_allowlist", "")


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _create(client: AsyncClient, token: str, provider: str, config: dict):
    """연결을 만들고 응답을 돌려준다."""
    return await client.post("/connections", headers=_h(token), json={
        "name": f"guard-{uuid.uuid4().hex[:8]}", "provider": provider, "config": config,
    })


@pytest.mark.parametrize(("provider", "config", "code"), [
    ("ncp", {"endpoint": "http://169.254.169.254", "bucket": "b"}, "connection_url_blocked"),
    ("openai", {"base_url": "http://169.254.169.254/latest", "model": "m"},
     "connection_url_blocked"),
    ("openai", {"base_url": "http://[fe80::1]:8080/v1", "model": "m"}, "connection_url_blocked"),
    ("openai", {"base_url": "http://[::ffff:169.254.169.254]/v1", "model": "m"},
     "connection_url_blocked"),
    ("local-llm", {"base_url": "http://metadata.google.internal/v1", "model": "m"},
     "connection_url_blocked"),
    ("local-llm", {"base_url": "http://127.0.0.1:8000/v1", "model": "m"},
     "connection_url_blocked"),
    ("local-llm", {"base_url": "http://[::1]:8000/v1", "model": "m"}, "connection_url_blocked"),
    ("local-llm", {"base_url": "http://localhost:8000/v1", "model": "m"},
     "connection_url_blocked"),
    ("local-llm", {"base_url": "http://2130706433/v1", "model": "m"}, "connection_url_blocked"),
    ("local-llm", {"base_url": "http://0x7f.1/v1", "model": "m"}, "connection_url_blocked"),
    ("local-llm", {"base_url": "http://0.0.0.0:8000/v1", "model": "m"},
     "connection_url_blocked"),
    ("ncp", {"endpoint": "http://postgres:5432", "bucket": "b"}, "connection_url_blocked"),
    ("maiu", {"base_url": "http://redis:6379", "workspace_name": "w"}, "connection_url_blocked"),
    ("local-llm", {"base_url": "http://api:8000/v1", "model": "m"}, "connection_url_blocked"),
    ("local-llm", {"base_url": "http://worker-qc/v1", "model": "m"}, "connection_url_blocked"),
    ("ncp", {"endpoint": "file:///etc/passwd", "bucket": "b"}, "connection_url_bad_scheme"),
    ("openai", {"base_url": "gopher://10.0.0.1:6379/_", "model": "m"},
     "connection_url_bad_scheme"),
    ("openai", {"base_url": "https://user:pw@api.example.com/v1", "model": "m"},
     "connection_url_userinfo"),
])
async def test_create_rejects_internal_and_non_http_addresses(
    client: AsyncClient, admin_token: str, provider: str, config: dict, code: str,
):
    """메타데이터·루프백·서버 내부 서비스 주소와 http(s)가 아닌 주소를 저장하지 않는지 확인한다."""
    r = await _create(client, admin_token, provider, config)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == code


@pytest.mark.parametrize(("provider", "config"), [
    ("local-llm", {"base_url": "http://10.0.0.5:8000/v1", "model": "m"}),
    ("local-llm", {"base_url": "http://172.20.1.9:8000/v1", "model": "m"}),
    ("local-llm", {"base_url": "http://192.168.0.10:8000/v1", "model": "m"}),
    ("ncp", {"endpoint": "https://storage.example.com", "bucket": "b"}),
    ("maiu", {"base_url": "https://mi.example.com", "workspace_name": "w"}),
    ("ncp", {"bucket": "b"}),
])
async def test_create_keeps_private_and_public_addresses(
    client: AsyncClient, admin_token: str, provider: str, config: dict,
):
    """사설 대역·공용 주소와 주소 없는 설정은 그대로 저장되는지 확인한다."""
    r = await _create(client, admin_token, provider, config)
    assert r.status_code == 201, r.text


async def test_allowlist_opens_a_named_internal_host(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """허용 목록에 적은 호스트만 내부 주소여도 저장되는지 확인한다."""
    monkeypatch.setattr(settings, "connection_host_allowlist", "vllm, 127.0.0.1")
    for url in ("http://vllm:8000/v1", "http://127.0.0.1:8000/v1"):
        r = await _create(client, admin_token, "local-llm", {"base_url": url, "model": "m"})
        assert r.status_code == 201, (url, r.text)
    r = await _create(client, admin_token, "local-llm",
                      {"base_url": "http://postgres:5432", "model": "m"})
    assert r.status_code == 422, r.text


async def test_update_rejects_a_blocked_address_and_keeps_the_old_one(
    client: AsyncClient, admin_token: str,
):
    """수정으로 막힌 주소를 넣으면 422이고 저장된 주소는 그대로인지 확인한다."""
    r = await _create(client, admin_token, "ncp", {
        "endpoint": "https://s3.example", "bucket": "b", "region": "kr"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    bad = await client.patch(f"/connections/{cid}", headers=_h(admin_token), json={
        "config": {"endpoint": "http://169.254.169.254", "bucket": "b"}})
    assert bad.status_code == 422, bad.text
    assert bad.json()["detail"] == "connection_url_blocked"
    listed = (await client.get("/connections", headers=_h(admin_token))).json()
    row = next(c for c in listed if c["id"] == cid)
    assert row["config"]["endpoint"] == "https://s3.example"


@pytest.mark.real_probe
async def test_bedrock_probe_without_keys_does_not_use_the_default_chain(monkeypatch):
    """키 없는 Bedrock 연결의 점검이 boto3를 부르지 않고 실패로 끝나는지 확인한다."""
    import boto3

    def _no_client(*args, **kwargs):
        """기본 자격증명 체인으로 넘어가는 호출을 실패로 만든다."""
        raise AssertionError("키 없이 boto3 클라이언트를 만들었다")

    monkeypatch.setattr(boto3, "client", _no_client)
    org = await default_org_id()
    async with SessionLocal() as s:
        conn = Connection(org_id=uuid.UUID(org), name=f"bedrock-{uuid.uuid4().hex[:8]}",
                          provider="bedrock", config={"region": "us-east-1", "model": "m"})
        s.add(conn)
        await s.commit()
        r = await conn_service.probe_one(conn)
    assert r.status == "fail" and r.detail == "액세스 키와 비밀 키가 없습니다"


async def test_bedrock_qc_call_without_keys_is_not_configured(monkeypatch):
    """품질검수의 Bedrock 호출이 키 없이 기본 자격증명 체인으로 넘어가지 않는지 확인한다."""
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("키 없이 boto3 클라이언트를 만들었다")))
    with pytest.raises(adapters.QcNotConfiguredError):
        await adapters._bedrock_call(
            adapters.ModelConfig(model="anthropic.claude-x", region="us-east-1"), "sys", "user")
