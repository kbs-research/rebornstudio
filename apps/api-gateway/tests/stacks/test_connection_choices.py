"""프로젝트 관리자가 캡션 품질검수의 모델 연결을 고를 수 있는지 보는 테스트.

연결 관리(`/connections`)는 최고관리자 전용이라 프로젝트 관리자는 그 목록을 받지 못한다.
설정 화면이 그 목록에서 고르게 하면 선택지가 비고, 저장된 연결이 "연결 없음"으로 보인다.
고르는 데 필요한 것은 이름뿐이라 설정·시크릿 없이 이름 목록을 준다.
없는 이름은 저장하지 않는다. 저장하면 이후 모든 AI 검사가 조용히 건너뛰어진다.
"""
import uuid
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Project
from httpx import AsyncClient
from tests._helpers import add_member, new_project


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _connection(
    client: AsyncClient, admin_token: str, provider: str = "anthropic", config: dict | None = None,
) -> str:
    """시크릿이 있는 연결을 하나 만들고 이름을 돌려준다."""
    name = f"qc-choice-{uuid.uuid4().hex[:8]}"
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": name, "provider": provider, "config": config or {"model": "m"},
        # Bedrock은 두 키가 모두 있어야 저장된다(`bedrock_keys_required`).
        "access_key": "AK_CHOICE" if provider == "bedrock" else "", "secret_key": "SECRET_CHOICE",
    })
    assert r.status_code == 201, r.text
    return name


async def test_project_admin_lists_connection_names_without_secrets(
    client: AsyncClient, admin_token: str,
):
    """프로젝트 관리자가 이름 목록을 받고 설정값·시크릿은 받지 않으며, 다른 역할은 403인지 확인한다."""
    name = await _connection(client, admin_token)
    pid = await new_project(client, admin_token, "qcchoice")
    _pa, patok = await add_member(client, admin_token, pid, "project_admin")
    _lb, lbtok = await add_member(client, admin_token, pid, "annotator")

    r = await client.get(f"/projects/{pid}/connection-choices", headers=_h(patok))
    assert r.status_code == 200, r.text
    rows = {c["name"]: c for c in r.json()}
    assert name in rows
    assert set(rows[name]) == {"name", "provider", "has_secret", "last_status", "adapter_keys"}
    assert rows[name]["has_secret"] is True
    assert "SECRET_CHOICE" not in r.text

    assert (await client.get(
        f"/projects/{pid}/connection-choices", headers=_h(lbtok),
    )).status_code == 403


async def test_unknown_connection_name_is_not_saved(client: AsyncClient, admin_token: str):
    """등록하지 않은 연결 이름을 422로 거절하고, 이미 저장된 이름을 다시 보내는 것은 막지 않는지 확인한다."""
    name = await _connection(client, admin_token)
    pid = await new_project(client, admin_token, "qcname")
    _pa, patok = await add_member(client, admin_token, pid, "project_admin")

    bad = await client.patch(f"/projects/{pid}", headers=_h(patok), json={
        "caption_qc": {"enabled": True, "adapter_key": "llm", "connection_name": "qa-nonexistent"},
    })
    assert bad.status_code == 422, bad.text
    assert bad.json()["detail"] == "connection_not_found"

    ok = await client.patch(f"/projects/{pid}", headers=_h(patok), json={
        "caption_qc": {"enabled": True, "adapter_key": "llm", "connection_name": name},
    })
    assert ok.status_code == 200, ok.text

    # 저장된 뒤 연결이 사라진 이름은 그대로 다시 보낼 수 있다. 다른 설정을 고치다 막히지 않게 한다.
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(pid))
        p.settings = {**(p.settings or {}), "caption_qc": {
            "enabled": True, "adapter_key": "llm", "connection_name": "gone-name",
        }}
        await s.commit()
    again = await client.patch(f"/projects/{pid}", headers=_h(patok), json={
        "caption_qc": {"enabled": False, "adapter_key": "llm", "connection_name": "gone-name"},
    })
    assert again.status_code == 200, again.text


async def test_caption_qc_choices_and_save_take_model_connections_only(
    client: AsyncClient, admin_token: str,
):
    """품질검수 선택지와 저장이 모델을 부를 수 있는 연결만 받는지 확인한다.

    저장소(ncp)·MI(maiu) 연결이 선택지에 섞이고 저장까지 되면 그 프로젝트의 모든 AI 검사가 실패한다.
    그러면 재시도 끝에 규칙 판정으로 확정되고, 연결 목록은 그 연결을 이 프로젝트가 쓴다고 센다.
    """
    model = await _connection(client, admin_token)
    bedrock = await _connection(client, admin_token, "bedrock", {"region": "us-east-1", "model": "m"})
    ncp = await _connection(client, admin_token, "ncp", {"endpoint": "https://e.example.com", "bucket": "b"})
    mi = await _connection(client, admin_token, "maiu", {"base_url": "https://mi.example.com"})
    pid = await new_project(client, admin_token, "qcprov")
    _pa, patok = await add_member(client, admin_token, pid, "project_admin")

    r = await client.get(
        f"/projects/{pid}/connection-choices", params={"purpose": "caption_qc"}, headers=_h(patok),
    )
    assert r.status_code == 200, r.text
    rows = {c["name"]: c for c in r.json()}
    assert model in rows and bedrock in rows
    assert ncp not in rows and mi not in rows
    assert rows[model]["adapter_keys"] == ["dummy", "llm"]
    assert rows[bedrock]["adapter_keys"] == ["bedrock", "dummy"]
    # 목적 없이 부르면 모두 준다. 품질검수에 쓸 수 없는 연결은 방식이 비어 있다.
    every = {c["name"]: c for c in (await client.get(
        f"/projects/{pid}/connection-choices", headers=_h(patok))).json()}
    assert every[ncp]["adapter_keys"] == []

    for name in (ncp, mi):
        bad = await client.patch(f"/projects/{pid}", headers=_h(patok), json={
            "caption_qc": {"enabled": True, "adapter_key": "llm", "connection_name": name},
        })
        assert bad.status_code == 422, bad.text
        assert bad.json()["detail"] == "connection_not_for_caption_qc"
    ok = await client.patch(f"/projects/{pid}", headers=_h(patok), json={
        "caption_qc": {"enabled": True, "adapter_key": "bedrock", "connection_name": bedrock},
    })
    assert ok.status_code == 200, ok.text
