"""원 작업자가 프로젝트에 없을 때 재작업이 어디로 가는지 보는 테스트.

재작업은 만든 사람이 고친다. 그 사람이 프로젝트에 없으면 누구도 그 작업을 진행할 수 없다.
본인은 403이고, 반납과 자동 회수가 없기 때문이다.
그래서 반려가 그 사람에게 재작업을 걸지 않고, 멤버 제거·나가기가 보유한 재작업을 큐로 돌린다.
다른 사람이 받으면 새 작업으로 시작한다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import WorkItem
from httpx import AsyncClient
from tests._helpers import (
    add_member,
    caption_payload,
    claim_review,
    new_project,
    seed_clip_work,
    upload_video,
)


def _h(tok: str) -> dict:
    """Bearer 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _item(wid: str) -> WorkItem:
    """작업 행을 새로 읽는다."""
    async with SessionLocal() as s:
        return await s.get(WorkItem, UUID(wid))


async def _submitted(client: AsyncClient, pid: str, annot: str) -> str:
    """라벨러가 작업 하나를 가져와 제출하고 그 ID를 돌려준다."""
    wid = (await client.post(f"/projects/{pid}/work/next", headers=_h(annot))).json()["work_item_id"]
    r = await client.post(f"/work/{wid}/submit", headers=_h(annot),
                          json={"payload": caption_payload("이전 작업자")})
    assert r.status_code == 200, r.text
    return wid


async def _reject(client: AsyncClient, pid: str, rev: str, wid: str) -> None:
    """검수자가 그 작업을 가져와 반려한다."""
    await claim_review(client, rev, pid, wid)
    r = await client.post(f"/work/{wid}/review", headers=_h(rev),
                          json={"decision": "reject", "reason": "다시"})
    assert r.status_code == 200, r.text


async def test_reject_after_labeler_removed_returns_to_queue(client: AsyncClient, admin_token: str):
    """제출한 사람이 멤버에서 빠진 뒤 반려하면 재작업을 걸지 않고 큐로 돌리며 그 사람에게 알리지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "rework-gone")
    uid_a, a = await add_member(client, admin_token, pid, "annotator")
    _, b = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = await _submitted(client, pid, a)
    r = await client.delete(f"/projects/{pid}/members/{uid_a}", headers=_h(admin_token))
    assert r.status_code in (200, 204), r.text

    await _reject(client, pid, rev, wid)

    item = await _item(wid)
    assert item.status == "available" and item.claimed_by is None, (item.status, item.claimed_by)
    assert item.rejection_count == 1
    # 다른 라벨러가 가져갈 수 있다.
    r = await client.post(f"/projects/{pid}/work/next", headers=_h(b))
    assert r.status_code == 200 and r.json()["work_item_id"] == wid, r.text


async def test_rework_taken_by_someone_else_starts_fresh(client: AsyncClient, admin_token: str):
    """멤버 제거로 큐에 돌아간 재작업을 다른 사람이 받으면 반려 횟수·이전 캡션·반려 사유 없이 새 작업으로 시작하는지 확인한다."""
    pid = await new_project(client, admin_token, "rework-fresh")
    uid_a, a = await add_member(client, admin_token, pid, "annotator")
    _, b = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = await _submitted(client, pid, a)
    await _reject(client, pid, rev, wid)
    assert (await client.delete(f"/projects/{pid}/members/{uid_a}",
                                headers=_h(admin_token))).status_code in (200, 204)
    assert (await _item(wid)).status == "available"

    r = await client.post(f"/projects/{pid}/work/next", headers=_h(b))
    assert r.json()["work_item_id"] == wid
    mine = (await client.get(f"/work/me?project_id={pid}", headers=_h(b))).json()
    row = next(w for w in mine if w["work_item_id"] == wid)
    assert row["rejection_count"] == 0
    assert row["payload"] == {}, "이전 작업자의 제출 캡션이 새 보유자에게 갔다"
    assert row["last_reject_reasons"] == []

    # 새 보유자의 다음 작업은 이 작업을 다시 연다.
    again = (await client.post(f"/projects/{pid}/work/next", headers=_h(b))).json()
    assert (again["work_item_id"], again["resumed"]) == (wid, True)


async def test_rework_returns_as_rework_to_its_author(client: AsyncClient, admin_token: str):
    """큐로 돌아간 재작업을 만든 사람이 다시 받으면 재작업 그대로인지 확인한다. 만든 사람은 반려 맥락을 안다."""
    pid = await new_project(client, admin_token, "rework-author")
    uid_a, a = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = await _submitted(client, pid, a)
    await _reject(client, pid, rev, wid)
    # 보유만 풀어 큐로 돌린다. 멤버 제거 뒤 다시 들어온 경우와 같은 상태다.
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(wid))
        item.status, item.claimed_by, item.claimed_at, item.expires_at = "available", None, None, None
        await s.commit()

    r = await client.post(f"/projects/{pid}/work/next", headers=_h(a))
    assert r.json()["work_item_id"] == wid
    item = await _item(wid)
    assert item.rejection_count == 1 and item.claimed_by == UUID(uid_a)
