"""같은 작업 행을 동시에 바꾸는 요청이 한 번만 반영되는지 보는 테스트. 제출·멤버 제거가 대상이다.

요청은 `asyncio.gather`로 동시에 보낸다. 앱은 요청마다 다른 DB 커넥션을 쓰므로 경합은 DB에서 실제로 일어난다.
보내기 전에 커넥션 풀을 채운다.
채우지 않으면 두 번째 요청이 커넥션을 여는 사이 첫 요청이 커밋까지 끝나 잠금이 없어도 요청이 차례로 처리되고, 테스트가 결함을 잡지 못한다.
"""
import asyncio
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent, Label, WorkItem
from httpx import AsyncClient
from sqlalchemy import func, select
from tests._helpers import (
    add_member,
    caption_payload,
    new_project,
    seed_clip_work,
    upload_video,
)


def _h(tok: str) -> dict:
    """Bearer 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _warm(client: AsyncClient, tok: str, n: int = 6) -> None:
    """커넥션 풀을 미리 채운다."""
    await asyncio.gather(*[client.get("/auth/me", headers=_h(tok)) for _ in range(n)])


async def test_concurrent_submits_record_one_label(client: AsyncClient, admin_token: str):
    """같은 작업을 동시에 네 번 제출해도 라벨 행과 제출 이벤트가 하나인지 확인한다.

    나머지 요청은 첫 제출이 바꾼 상태를 보고 409 `work_item_is_submitted`로 끝난다.
    잠그지 않으면 모든 요청이 `claimed`를 보고 통과해 제출 수가 부풀려진다.
    """
    pid = await new_project(client, admin_token, "race-submit")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (await client.post(f"/projects/{pid}/work/next", headers=_h(annot))).json()["work_item_id"]
    await _warm(client, annot)

    results = await asyncio.gather(*[
        client.post(f"/work/{wid}/submit", headers=_h(annot),
                    json={"payload": caption_payload(f"동시 {i}")})
        for i in range(4)
    ])
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409, 409, 409], [r.text[:120] for r in results]
    assert {r.json()["detail"] for r in results if r.status_code == 409} == {"work_item_is_submitted"}

    async with SessionLocal() as s:
        labels = await s.scalar(
            select(func.count()).select_from(Label).where(Label.work_item_id == UUID(wid))
        )
        events = await s.scalar(
            select(func.count()).select_from(DomainEvent).where(
                DomainEvent.event_type == "work.submitted", DomainEvent.target_id == UUID(wid),
            )
        )
    assert (labels, events) == (1, 1)


async def test_member_removal_racing_submit_leaves_no_orphan(
    client: AsyncClient, admin_token: str
):
    """멤버 제거와 제출이 겹쳐도 보유자 없는 제출 작업이 생기지 않는지 확인한다.

    제출이 먼저면 제출이 남고 보유자도 남는다. 제거가 먼저면 작업이 대기로 돌아가고 제출은 거절된다.
    """
    pid = await new_project(client, admin_token, "race-remove")
    uid, annot = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (await client.post(f"/projects/{pid}/work/next", headers=_h(annot))).json()["work_item_id"]
    await _warm(client, admin_token)

    rm, sub = await asyncio.gather(
        client.delete(f"/projects/{pid}/members/{uid}", headers=_h(admin_token)),
        client.post(f"/work/{wid}/submit", headers=_h(annot), json={"payload": caption_payload()}),
    )
    assert rm.status_code == 204, rm.text
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(wid))
    if sub.status_code == 200:
        assert item.status == "submitted" and item.claimed_by == UUID(uid)
    else:
        assert item.status == "available" and item.claimed_by is None


async def test_submitted_without_labeler_is_reviewable(client: AsyncClient, admin_token: str):
    """라벨러가 비어 있는 제출도 검수자가 가져갈 수 있는지 확인한다.

    `claimed_by != 호출한 사용자`만 걸면 SQL에서 NULL 비교가 참이 아니라서 그 제출은 대기 수에도 가져오기에도 잡히지 않는다.
    """
    pid = await new_project(client, admin_token, "race-orphan")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    _, wid = await seed_clip_work(pid, vid)
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(wid))
        item.status = "submitted"
        item.claimed_by = None
        await s.commit()

    count = (await client.get(f"/projects/{pid}/review/available-count", headers=_h(rev))).json()
    assert count["count"] == 1
    r = await client.post(f"/projects/{pid}/review/next", headers=_h(rev))
    assert r.status_code == 200, r.text
    assert r.json()["work_item_id"] == wid

