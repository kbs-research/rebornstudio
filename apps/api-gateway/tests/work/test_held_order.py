"""보유 목록과 다음 작업의 순서 회귀 테스트.

라벨링(`GET /work/me`)과 검수(`GET /projects/{id}/review/mine`) 보유 목록은 모두 `work_service.held_order`로 정렬한다.
순서는 재작업 먼저, 그다음 가져온 순이다. 재작업 구분이 없는 검수 목록은 가져온 순만 쓴다.
다음 작업(`work/next`·`review/next`)은 보유한 작업이 있으면 이 순서의 첫 건을 다시 연다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import WorkItem
from httpx import AsyncClient
from sqlalchemy import update
from tests._helpers import add_member, new_project, seed_clip_work, upload_video


async def _set_items(values: dict[str, dict]) -> None:
    """작업마다 주어진 컬럼 값을 덮어쓴다."""
    async with SessionLocal() as s:
        for wid, v in values.items():
            await s.execute(update(WorkItem).where(WorkItem.work_item_id == UUID(wid)).values(**v))
        await s.commit()


async def test_held_list_puts_rework_first_then_claim_order(client: AsyncClient, admin_token: str):
    """재작업이 가져온 시각과 무관하게 먼저 오고, 나머지는 먼저 가져온 순인지 확인한다.

    이전 버전이 남긴 지난 만료 시각은 순서에 영향을 주지 않는다.
    """
    pid = await new_project(client, admin_token, "held-order")
    uid, tok = await add_member(client, admin_token, pid, "annotator")
    hdr = {"Authorization": f"Bearer {tok}"}
    vid = await upload_video(client, admin_token, pid)
    ids = [
        (await seed_clip_work(pid, vid, start=i * 10.0, end=i * 10.0 + 5.0))[1]
        for i in range(4)
    ]
    now = datetime.now(UTC)
    held = {"status": "claimed", "claimed_by": UUID(uid)}
    await _set_items({
        ids[0]: {**held, "rejection_count": 0, "claimed_at": now - timedelta(minutes=10)},
        ids[1]: {**held, "rejection_count": 1, "claimed_at": now - timedelta(minutes=5)},
        ids[2]: {**held, "rejection_count": 0, "claimed_at": now - timedelta(minutes=30),
                 "expires_at": now - timedelta(hours=1)},
        ids[3]: {**held, "rejection_count": 2, "claimed_at": now - timedelta(minutes=1)},
    })

    mine = (await client.get("/work/me", headers=hdr, params={"project_id": pid})).json()
    assert [m["work_item_id"] for m in mine] == [ids[1], ids[3], ids[2], ids[0]], mine

    # 다음 작업은 목록의 첫 건을 다시 연다.
    r = await client.post(f"/projects/{pid}/work/next", headers=hdr)
    assert r.status_code == 200, r.text
    assert (r.json()["work_item_id"], r.json()["resumed"]) == (ids[1], True)


async def test_review_held_list_keeps_holds_without_expiry(
    client: AsyncClient, admin_token: str
):
    """만료 시각이 없거나 지난 검수 보유도 목록에 싣는지 확인한다. 검수 보유에는 끝나는 시각이 없다."""
    pid = await new_project(client, admin_token, "held-review-null")
    rev_uid, rev_tok = await add_member(client, admin_token, pid, "reviewer")
    hdr = {"Authorization": f"Bearer {rev_tok}"}
    vid = await upload_video(client, admin_token, pid)
    ids = [
        (await seed_clip_work(pid, vid, start=i * 10.0, end=i * 10.0 + 5.0))[1]
        for i in range(2)
    ]
    now = datetime.now(UTC)
    held = {"status": "submitted", "submitted_at": now, "review_claimed_by": UUID(rev_uid)}
    await _set_items({
        ids[0]: {**held, "review_claimed_at": now - timedelta(minutes=20), "review_expires_at": None},
        ids[1]: {**held, "review_claimed_at": now - timedelta(minutes=10),
                 "review_expires_at": now - timedelta(hours=2)},
    })

    r = await client.get(f"/projects/{pid}/review/mine", headers=hdr)
    assert [m["work_item_id"] for m in r.json()] == [ids[0], ids[1]], r.json()


async def test_review_held_list_orders_by_claim_time_without_rework(
    client: AsyncClient, admin_token: str
):
    """검수 보유 목록이 먼저 가져온 순인지 확인한다.

    검수에는 재작업 구분이 없어 반려 이력이 있는 작업도 앞으로 올라가지 않는다.
    다음 검수는 이 순서의 첫 건을 다시 연다.
    """
    pid = await new_project(client, admin_token, "held-review")
    rev_uid, rev_tok = await add_member(client, admin_token, pid, "reviewer")
    hdr = {"Authorization": f"Bearer {rev_tok}"}
    vid = await upload_video(client, admin_token, pid)
    ids = [
        (await seed_clip_work(pid, vid, start=i * 10.0, end=i * 10.0 + 5.0))[1]
        for i in range(3)
    ]

    now = datetime.now(UTC)
    held = {"status": "submitted", "submitted_at": now, "review_claimed_by": UUID(rev_uid)}
    await _set_items({
        ids[0]: {**held, "rejection_count": 2, "review_claimed_at": now - timedelta(minutes=10)},
        ids[1]: {**held, "rejection_count": 0, "review_claimed_at": now - timedelta(minutes=30)},
        ids[2]: {**held, "rejection_count": 0, "review_claimed_at": now - timedelta(minutes=20)},
    })

    r = await client.get(f"/projects/{pid}/review/mine", headers=hdr)
    assert r.status_code == 200, r.text
    assert [m["work_item_id"] for m in r.json()] == [ids[1], ids[2], ids[0]], r.json()

    r = await client.post(f"/projects/{pid}/review/next", headers=hdr)
    assert r.status_code == 200, r.text
    assert (r.json()["work_item_id"], r.json()["resumed"]) == (ids[1], True)
