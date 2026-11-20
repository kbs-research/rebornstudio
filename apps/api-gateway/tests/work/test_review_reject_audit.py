"""검수자의 반려가 감사 기록(`domain_events`)에 누가·무엇을·왜로 남는지 보는 테스트.

반려는 라벨러의 반려율과 반려 횟수로 이어지는 판정이라 이의 제기의 근거가 된다.
판정 행(`reviews`)과 별개로 감사 행에도 행위자·대상·사유가 있어야 감사 화면에서 추적할 수 있다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent, WorkItem
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import (
    add_member,
    caption_payload,
    claim_review,
    new_project,
    seed_clip_work,
    upload_video,
)


def _h(tok: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def test_reject_writes_audit_row_with_actor_target_and_reason(
    client: AsyncClient, admin_token: str,
):
    """반려 한 번이 `review.reject` 감사 행 하나를 남기는지 확인한다.

    행위자는 검수자, 대상은 그 클립의 작업이고, payload에는 사유 문장·사유 코드·누적 반려 수가 있다.
    """
    pid = await new_project(client, admin_token, "rej-audit")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    rev_uid, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    clip_id, wid = await seed_clip_work(pid, vid)

    r = await client.post(f"/projects/{pid}/work/next", headers=_h(annot))
    assert r.status_code == 200 and r.json()["work_item_id"] == wid, r.text
    r = await client.post(f"/work/{wid}/submit", headers=_h(annot),
                          json={"payload": caption_payload("반려 감사")})
    assert r.status_code == 200, r.text
    await claim_review(client, rev, pid, wid)

    reason = "화자 이름이 자막과 다릅니다"
    codes = ["deident_insufficient", "caption_inaccurate"]
    r = await client.post(f"/work/{wid}/review", headers=_h(rev),
                          json={"decision": "reject", "reason": reason, "reason_codes": codes})
    assert r.status_code == 200, r.text

    async with SessionLocal() as s:
        events = (await s.scalars(
            select(DomainEvent).where(
                DomainEvent.target_id == UUID(wid),
                DomainEvent.event_type == "review.reject",
            )
        )).all()
        item = await s.get(WorkItem, UUID(wid))

    assert len(events) == 1, [e.event_type for e in events]
    ev = events[0]
    assert str(ev.actor_user_id) == rev_uid
    assert ev.target_kind == "work_item"
    assert str(ev.project_id) == pid
    assert ev.kind == "audit"
    assert ev.payload["reason"] == reason
    assert ev.payload["reason_codes"] == codes
    assert ev.payload["rejection_count"] == 1
    # 감사 행은 작업을 가리킨다. 작업에서 클립까지 이어지는지 확인한다.
    assert str(item.clip_id) == clip_id


async def test_reject_without_reason_is_422_and_nothing_changes(
    client: AsyncClient, admin_token: str,
):
    """사유 코드와 보충 설명이 모두 없는 반려가 422이고 반려 수가 늘지 않는지 확인한다.

    지적 문장은 틀린 위치만 표시하고 틀린 내용(사유)은 알려 주지 않는다. 사유가 없으면 라벨러는 재작업에서 고칠 것을 알 수 없다.
    반려는 반려율과 반려 횟수에 들어가는 판정이라 사유 없이 받지 않는다.
    """
    pid = await new_project(client, admin_token, "rej-empty")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    _, wid = await seed_clip_work(pid, vid)
    assert (await client.post(f"/projects/{pid}/work/next", headers=_h(annot))).status_code == 200
    r = await client.post(f"/work/{wid}/submit", headers=_h(annot),
                          json={"payload": caption_payload("빈 반려")})
    assert r.status_code == 200, r.text
    await claim_review(client, rev, pid, wid)

    for body in (
        {"decision": "reject", "reason_codes": [], "flagged_sentences": []},
        {"decision": "reject", "reason": "   ", "flagged_sentences": ["빈 반려"]},
    ):
        r = await client.post(f"/work/{wid}/review", headers=_h(rev), json=body)
        assert r.status_code == 422, r.text
        assert r.json()["detail"][0]["loc"] == ["body", "reason_codes"], r.text
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(wid))
    assert item.status == "submitted" and item.rejection_count == 0

    # 보충 설명만 있어도 반려된다. 사유 칩이 없는 프로젝트도 반려할 수 있어야 한다.
    r = await client.post(f"/work/{wid}/review", headers=_h(rev),
                          json={"decision": "reject", "reason": "고유명사 표기 확인"})
    assert r.status_code == 200, r.text
