"""연결 수정 감사 기록이 바뀐 설정 키, 주소의 전후 값, 시크릿 교체 여부를 남기는지 보는 테스트.

주소를 바꾸면 저장된 자격증명이 새 주소로 나간다. 그래서 그 변경이 감사 기록에 보여야 한다.
시크릿 값 자체는 기록에 남기지 않는다.
"""
import uuid

from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent
from httpx import AsyncClient
from sqlalchemy import select


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _updated_payload(cid: str) -> dict:
    """연결 하나의 `connection.updated` 감사 기록 payload를 읽는다."""
    async with SessionLocal() as db:
        ev = (await db.scalars(
            select(DomainEvent)
            .where(DomainEvent.event_type == "connection.updated",
                   DomainEvent.target_id == uuid.UUID(cid))
        )).one()
    return ev.payload


async def test_update_audit_records_address_change_and_secret_replacement(
    client: AsyncClient, admin_token: str,
):
    """주소와 시크릿을 바꾸면 감사 기록에 바뀐 키, 주소 전후 값, 시크릿 교체가 남는지 확인한다."""
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": f"audit-{uuid.uuid4().hex[:8]}", "provider": "ncp",
        "config": {"endpoint": "https://s3.example.com", "bucket": "sample-bucket",
                   "region": "kr"},
    })
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    upd = await client.patch(f"/connections/{cid}", headers=_h(admin_token), json={
        "config": {"endpoint": "https://s3.other.example.com", "bucket": "sample-bucket-2",
                   "region": "kr"},
        "access_key": "AK-SAMPLE", "secret_key": "SK-SAMPLE",
    })
    assert upd.status_code == 200, upd.text

    payload = await _updated_payload(cid)
    assert payload["secret_replaced"] is True
    assert payload["cleared_secret"] is False
    assert payload["config_keys_changed"] == ["bucket", "endpoint"]
    assert payload["address_changes"] == {
        "endpoint": {"before": "https://s3.example.com", "after": "https://s3.other.example.com"},
    }
    assert "SK-SAMPLE" not in str(payload) and "AK-SAMPLE" not in str(payload)


async def test_update_audit_without_config_change_has_no_address_changes(
    client: AsyncClient, admin_token: str,
):
    """시크릿만 지우면 바뀐 키가 없고 주소 전후 값도 남지 않는지 확인한다."""
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": f"audit-{uuid.uuid4().hex[:8]}", "provider": "local-llm",
        "config": {"base_url": "https://llm.example.com/v1", "model": "m"},
        "access_key": "AK-SAMPLE", "secret_key": "SK-SAMPLE",
    })
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    upd = await client.patch(f"/connections/{cid}", headers=_h(admin_token),
                             json={"clear_secret": True})
    assert upd.status_code == 200, upd.text

    payload = await _updated_payload(cid)
    assert payload["cleared_secret"] is True
    assert payload["secret_replaced"] is False
    assert payload["config_keys_changed"] == []
    assert "address_changes" not in payload
