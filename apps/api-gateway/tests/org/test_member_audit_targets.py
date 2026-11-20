"""멤버·프로젝트 감사 기록의 대상과 내용을 보는 테스트.

감사 기록에는 누가 누구에게 무엇을 했는지 남아야 한다.
대상 ID가 프로젝트를 가리키거나 payload가 비면 기록에서 그 사람을 찾을 수 없다.
바뀐 것이 없는 수정은 기록하지 않는다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import add_member, make_account, new_project
from tests.conftest import uniq


def _h(tok: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _events(pid: str, event_type: str) -> list[DomainEvent]:
    """이 프로젝트의 그 종류 감사 이벤트를 최신순으로 돌려준다."""
    async with SessionLocal() as s:
        return list((await s.scalars(
            select(DomainEvent)
            .where(DomainEvent.project_id == UUID(pid), DomainEvent.event_type == event_type)
            .order_by(DomainEvent.occurred_at.desc())
        )).all())


async def test_member_events_target_the_person(client: AsyncClient, admin_token: str):
    """초대·역할 변경 기록의 대상이 프로젝트 ID가 아니라 그 사람인지 확인한다."""
    pid = await new_project(client, admin_token, "aud-member")
    inv_id, email, _pwd, _tok = await make_account(client, admin_token, "audinv")
    r = await client.post(
        f"/projects/{pid}/members/invite", headers=_h(admin_token),
        json={"email": email, "role": "reviewer"},
    )
    assert r.status_code == 200, r.text
    r = await client.patch(
        f"/projects/{pid}/members/{inv_id}/role", headers=_h(admin_token), json={"role": "annotator"},
    )
    assert r.status_code == 200, r.text

    for event_type, who in (("member.invited", inv_id), ("member.role_changed", inv_id)):
        ev = await _events(pid, event_type)
        assert ev, event_type
        assert (ev[0].target_kind, str(ev[0].target_id)) == ("user", who), event_type


async def test_no_op_project_update_leaves_no_audit_row(client: AsyncClient, admin_token: str):
    """모르는 키를 보내거나 같은 값으로 저장하면 `project.updated`가 남지 않는지 확인한다.

    모르는 키는 422로 거절하므로 기록할 변경이 없다.
    """
    pid = await new_project(client, admin_token, "aud-noop")
    r = await client.patch(f"/projects/{pid}", headers=_h(admin_token), json={"unknown_key": 1})
    assert r.status_code == 422, r.text
    for body in ({"name": "aud-noop-name"}, {"name": "aud-noop-name"}):
        r = await client.patch(f"/projects/{pid}", headers=_h(admin_token), json=body)
        assert r.status_code == 200, r.text
    ev = await _events(pid, "project.updated")
    assert len(ev) == 1
    assert ev[0].payload["changed"]["name"]["to"] == "aud-noop-name"

    # 캡션 품질검수 설정을 바꾸면 그 변경을 기록한다. 같은 값이면 기록하지 않는다.
    # 요청은 켜기와 검사 방식, 모델 연결만 받으므로 응답에서 그 세 키만 골라 보낸다.
    qc = (await client.get(f"/projects/{pid}", headers=_h(admin_token))).json()["caption_qc"]
    cur = {k: qc[k] for k in ("enabled", "adapter_key", "connection_name")}
    r = await client.patch(f"/projects/{pid}", headers=_h(admin_token), json={"caption_qc": cur})
    assert r.status_code == 200, r.text
    assert len(await _events(pid, "project.updated")) == 1
    flipped = {**cur, "enabled": not cur["enabled"]}
    r = await client.patch(f"/projects/{pid}", headers=_h(admin_token), json={"caption_qc": flipped})
    assert r.status_code == 200, r.text
    ev = await _events(pid, "project.updated")
    assert len(ev) == 2 and "caption_qc" in ev[0].payload["changed"]


async def test_past_contract_deadline_is_rejected(client: AsyncClient, admin_token: str):
    """지난 날짜를 마감일로 새로 정하면 422이고 값이 바뀌지 않는지, 오늘 이후는 저장되는지 확인한다."""
    pid = await new_project(client, admin_token, "aud-deadline")
    _, pa = await add_member(client, admin_token, pid, "project_admin")
    r = await client.patch(f"/projects/{pid}", headers=_h(pa), json={"contract_deadline": "2020-01-01"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["loc"][-1] == "contract_deadline"
    got = (await client.get(f"/projects/{pid}", headers=_h(pa))).json()
    assert got["contract_deadline"] is None

    r = await client.patch(f"/projects/{pid}", headers=_h(pa), json={"contract_deadline": "2099-01-01"})
    assert r.status_code == 200, r.text
    assert r.json()["contract_deadline"] == "2099-01-01"

    r = await client.post("/projects", headers=_h(admin_token), json={
        "slug": uniq("dl").split("@")[0], "name": "dl",
        "contract_deadline": "2020-01-01",
    })
    assert r.status_code == 422, r.text
