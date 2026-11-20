"""사람별 실적의 승인 수가 작업마다 첫 승인 하나만 세는지 보는 테스트.

승인을 판정 행 수로 세면 관리자가 캡션을 고쳐 다시 승인할 때 한 제출의 승인을 한 번 더 센다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Label, Review
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import (
    add_member,
    caption_payload,
    claim_review,
    latest_label_id,
    new_project,
    seed_clip_work,
    upload_video,
)


def _h(tok: str) -> dict:
    """Bearer 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _submit(client: AsyncClient, pid: str, vid: str, tok: str, start: float) -> str:
    """클립 작업 하나를 만들어 라벨러가 가져와 제출하고 그 ID를 돌려준다."""
    await seed_clip_work(pid, vid, start=start, end=start + 5.0)
    r = await client.post(f"/projects/{pid}/work/next", headers=_h(tok))
    assert r.status_code == 200, r.text
    wid = r.json()["work_item_id"]
    r = await client.post(f"/work/{wid}/submit", headers=_h(tok), json={"payload": caption_payload()})
    assert r.status_code == 200, r.text
    return wid


async def _approve(client: AsyncClient, pid: str, rev: str, wid: str, *, modify: bool = False) -> None:
    """검수자가 가져와 승인한다. `modify`면 고쳐서 승인한다."""
    await claim_review(client, rev, pid, wid)
    body: dict = {"decision": "approve"}
    if modify:
        body = {"decision": "modify_and_approve",
                "modified_label_data": caption_payload("검수자가 고친 캡션입니다."),
                "base_label_id": await latest_label_id(client, rev, wid), "reason": "수정"}
    r = await client.post(f"/work/{wid}/review", headers=_h(rev), json=body)
    assert r.status_code == 200, r.text


async def _admin_revise(wid: str, admin_id: str) -> None:
    """보류분 캡션 고치기(`caption_qc.hold.revise`)가 남기는 행을 그대로 만든다.

    고친 버전은 직전 개정을 부모로 둔 관리자 이름의 새 개정이고, 그 개정을 가리키는 수정 후 승인 판정이 더해진다.
    """
    async with SessionLocal() as s:
        prev = await s.scalar(select(Label).where(Label.work_item_id == UUID(wid))
                              .order_by(Label.submitted_at.desc()).limit(1))
        label = Label(work_item_id=UUID(wid), parent_label_id=prev.label_id,
                      submitted_by=UUID(admin_id), label_data=caption_payload("관리자 수정"),
                      diff_chars_count=1)
        s.add(label)
        await s.flush()
        s.add(Review(work_item_id=UUID(wid), label_id=label.label_id, reviewer_id=UUID(admin_id),
                     verdict="modify_and_approve", reason_codes=[]))
        await s.commit()


async def _row(client: AsyncClient, pid: str, admin_token: str, uid: str) -> dict:
    """사람별 실적 표에서 한 사람의 행을 읽는다."""
    r = await client.get(f"/projects/{pid}/dashboard/labeler-performance", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    return next(x for x in r.json() if x["user_id"] == uid)


async def test_admin_revise_does_not_count_a_second_approval(client: AsyncClient, admin_token: str):
    """승인된 제출을 관리자가 고쳐 다시 승인해도 그 제출의 승인을 한 번만 세는지(승인율 4/4) 확인한다."""
    pid = await new_project(client, admin_token, "self-revise")
    vid = await upload_video(client, admin_token, pid)
    lab_id, lab = await add_member(client, admin_token, pid, "annotator")
    rev_id, rev = await add_member(client, admin_token, pid, "reviewer")
    admin_id = (await client.get("/auth/me", headers=_h(admin_token))).json()["user_id"]
    wids = [await _submit(client, pid, vid, lab, 5.0 * i) for i in range(4)]
    for i, wid in enumerate(wids):
        await _approve(client, pid, rev, wid, modify=(i == 1))
    await _admin_revise(wids[0], admin_id)   # 검수자 승인 위에 관리자가 고친다
    await _admin_revise(wids[1], admin_id)   # 검수자 수정 후 승인 위에 관리자가 고친다

    row = await _row(client, pid, admin_token, lab_id)
    assert (row["submitted"], row["approved"], row["rejected"]) == (4, 4, 0), row
    assert row["approval_rate"] == 100.0
    rows = (await client.get(f"/projects/{pid}/dashboard/labeler-performance",
                             headers=_h(admin_token))).json()
    by = {r["user_id"]: r for r in rows}
    reviewer = by.get(rev_id)
    assert reviewer is None or reviewer["approved"] == 0, "검수자의 개정 위 고침이 검수자 승인으로 세졌다"
