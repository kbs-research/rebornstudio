"""일할 수 없게 된 사람의 보유를 푸는 테스트. 멤버 제거·나가기·계정 정지가 라벨링과 검수 보유를 함께 푼다.

보유에는 끝나는 시각도 자동 회수도 없다. 풀지 않으면 그 작업은 아무도 진행할 수 없다.
푼 작업의 ID와 수는 그 이벤트의 감사 기록에 남는다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import WorkItem
from httpx import AsyncClient
from sqlalchemy import update
from tests._helpers import (
    add_member,
    events,
    invite_user,
    new_project,
    seed_clip_work,
    upload_video,
)


def _h(tok: str) -> dict:
    """Bearer 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _hold(wid: str, *, labeling_by: str | None = None, review_by: str | None = None,
                rejections: int = 0) -> None:
    """작업 하나를 그 사람의 라벨링 보유나 검수 보유로 만든다."""
    values: dict = {"rejection_count": rejections}
    if labeling_by:
        values.update(status="claimed", claimed_by=UUID(labeling_by), draft={"caption": "쓰던 것"})
    if review_by:
        values.update(status="submitted", review_claimed_by=UUID(review_by),
                      review_draft={"marks": []})
    async with SessionLocal() as s:
        await s.execute(update(WorkItem).where(WorkItem.work_item_id == UUID(wid)).values(**values))
        await s.commit()


async def _item(wid: str) -> WorkItem:
    """작업 행을 새로 읽는다."""
    async with SessionLocal() as s:
        return await s.get(WorkItem, UUID(wid))


async def test_removal_frees_labeling_and_review_holds_and_records_them(
    client: AsyncClient, admin_token: str,
):
    """멤버 제거가 라벨링 보유(재작업 포함)와 검수 보유를 모두 대기열로 돌리고 감사 기록에 남기는지 확인한다.

    재작업은 반려 횟수를 그대로 둔 채 대기열로 간다. 다른 사람이 받으면 새 작업으로 시작한다.
    """
    pid = await new_project(client, admin_token, "rel-rm")
    vid = await upload_video(client, admin_token, pid)
    uid, _tok = await add_member(client, admin_token, pid, "annotator")
    _c1, fresh = await seed_clip_work(pid, vid, start=0.0, end=5.0)
    _c2, rework = await seed_clip_work(pid, vid, start=5.0, end=10.0)
    _c3, review = await seed_clip_work(pid, vid, start=10.0, end=15.0)
    await _hold(fresh, labeling_by=uid)
    await _hold(rework, labeling_by=uid, rejections=2)
    await _hold(review, review_by=uid)

    r = await client.delete(f"/projects/{pid}/members/{uid}", headers=_h(admin_token))
    assert r.status_code in (200, 204), r.text

    for wid in (fresh, rework):
        item = await _item(wid)
        assert (item.status, item.claimed_by, item.draft) == ("available", None, None)
    assert (await _item(rework)).rejection_count == 2
    rv = await _item(review)
    assert (rv.status, rv.review_claimed_by, rv.review_draft) == ("submitted", None, None)

    [ev] = await events(event_type="member.removed", target_id=uid)
    assert sorted(ev["payload"]["released"]["labeling"]) == sorted([fresh, rework])
    assert ev["payload"]["released"]["review"] == [review]
    assert ev["payload"]["released_counts"] == {"labeling": 2, "review": 1}


async def test_leaving_frees_review_holds_too(client: AsyncClient, admin_token: str):
    """본인이 나갈 때도 검수 보유가 풀리고, 응답의 수와 감사 기록이 두 종류를 함께 세는지 확인한다."""
    pid = await new_project(client, admin_token, "rel-left")
    vid = await upload_video(client, admin_token, pid)
    uid, tok = await add_member(client, admin_token, pid, "reviewer")
    _c1, review = await seed_clip_work(pid, vid)
    await _hold(review, review_by=uid)

    r = await client.post(f"/projects/{pid}/me/leave", headers=_h(tok), json={"reason": None})
    assert r.status_code == 200 and r.json()["released"] == 1, r.text
    assert (await _item(review)).review_claimed_by is None
    [ev] = await events(event_type="member.left", project_id=pid)
    assert ev["payload"]["released_counts"] == {"labeling": 0, "review": 1}


async def test_suspension_frees_holds_in_every_project(client: AsyncClient, admin_token: str):
    """계정 정지가 모든 프로젝트의 라벨링·검수 보유를 풀고 감사 기록에 남기며, 정지를 풀어도 되돌리지 않는지 확인한다.

    다른 사람의 보유는 건드리지 않는다.
    """
    p1 = await new_project(client, admin_token, "rel-sus1")
    p2 = await new_project(client, admin_token, "rel-sus2")
    v1 = await upload_video(client, admin_token, p1)
    v2 = await upload_video(client, admin_token, p2)
    uid, _tok = await add_member(client, admin_token, p1, "project_admin")
    await invite_user(client, admin_token, p2, uid, "annotator")
    other, _ = await add_member(client, admin_token, p1, "annotator")
    _a, lab1 = await seed_clip_work(p1, v1, start=0.0, end=5.0)
    _b, rev1 = await seed_clip_work(p1, v1, start=5.0, end=10.0)
    _c, lab2 = await seed_clip_work(p2, v2, start=0.0, end=5.0)
    _d, others = await seed_clip_work(p1, v1, start=10.0, end=15.0)
    await _hold(lab1, labeling_by=uid, rejections=1)
    await _hold(rev1, review_by=uid)
    await _hold(lab2, labeling_by=uid)
    await _hold(others, labeling_by=other)

    r = await client.patch(f"/admin/users/{uid}/status", headers=_h(admin_token),
                           json={"status": "suspended"})
    assert r.status_code == 200, r.text

    for wid in (lab1, lab2):
        assert ((await _item(wid)).status, (await _item(wid)).claimed_by) == ("available", None)
    assert (await _item(rev1)).review_claimed_by is None
    assert str((await _item(others)).claimed_by) == other, "다른 사람의 보유를 풀었다"
    [ev] = await events(event_type="user.status_changed", target_id=uid)
    assert ev["payload"]["to"] == "suspended"
    assert sorted(ev["payload"]["released"]["labeling"]) == sorted([lab1, lab2])
    assert ev["payload"]["released"]["review"] == [rev1]
    assert ev["payload"]["released_counts"] == {"labeling": 2, "review": 1}

    # 정지를 풀어도 보유는 돌아오지 않는다. 감사 기록에도 푼 작업이 없다.
    r = await client.patch(f"/admin/users/{uid}/status", headers=_h(admin_token),
                           json={"status": "active"})
    assert r.status_code == 200, r.text
    assert (await _item(lab1)).claimed_by is None
    latest = (await events(event_type="user.status_changed", target_id=uid))[0]
    assert latest["payload"] == {"from": "suspended", "to": "active"}
