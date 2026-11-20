"""가져오기 순서 테스트. 라벨링은 재작업 다음 오래된 순, 검수는 제출한 순으로 가져간다.

`work_items.priority` 컬럼은 남아 있지만 정렬에 쓰지 않는다. 그래서 각 테스트는 나중 작업의
점수를 높게 심어 두고도 순서가 바뀌지 않는지 본다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Label, WorkItem
from httpx import AsyncClient
from tests._helpers import add_member, new_project, seed_clip_work, upload_video


async def _set(work_item_id: str, **values) -> None:
    """작업 행의 열을 바로 바꾼다."""
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(work_item_id))
        for k, v in values.items():
            setattr(item, k, v)
        await s.commit()


async def test_claim_takes_the_oldest_work_first(client: AsyncClient, admin_token: str):
    """점수와 관계없이 먼저 만든 작업을 먼저 가져가는지 확인한다."""
    pid = await new_project(client, admin_token, "claim-old")
    vid = await upload_video(client, admin_token, pid)
    _, older = await seed_clip_work(pid, vid, start=0.0, end=5.0)
    _, newer = await seed_clip_work(pid, vid, start=5.0, end=10.0)
    now = datetime.now(UTC)
    await _set(older, created_at=now - timedelta(hours=2))
    await _set(newer, created_at=now - timedelta(hours=1), priority=9999)

    _, tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.post(f"/projects/{pid}/work/next", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    assert r.json()["work_item_id"] == older


async def test_claim_gives_back_own_rework_first(client: AsyncClient, admin_token: str):
    """대기열에 돌아온 재작업 중 호출한 사람이 제출했던 작업을 오래된 작업보다 먼저 주는지 확인한다.

    반려 맥락(사유·지적 문장)을 아는 사람이 다시 받아야 같은 반려를 반복하지 않는다.
    """
    pid = await new_project(client, admin_token, "claim-rework")
    vid = await upload_video(client, admin_token, pid)
    _, older = await seed_clip_work(pid, vid, start=0.0, end=5.0)
    _, rework = await seed_clip_work(pid, vid, start=5.0, end=10.0)
    now = datetime.now(UTC)
    await _set(older, created_at=now - timedelta(hours=2), priority=9999)
    await _set(rework, created_at=now - timedelta(hours=1), rejection_count=1)

    uid, tok = await add_member(client, admin_token, pid, "annotator")
    async with SessionLocal() as s:
        s.add(Label(work_item_id=UUID(rework), submitted_by=UUID(uid), label_data={}))
        await s.commit()
    r = await client.post(f"/projects/{pid}/work/next", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    assert r.json()["work_item_id"] == rework


async def test_review_claim_follows_submission_order(client: AsyncClient, admin_token: str):
    """검수는 점수와 관계없이 먼저 제출된 작업부터 가져가는지 확인한다."""
    pid = await new_project(client, admin_token, "review-order")
    vid = await upload_video(client, admin_token, pid)
    _, first = await seed_clip_work(pid, vid, start=0.0, end=5.0)
    _, second = await seed_clip_work(pid, vid, start=5.0, end=10.0)
    now = datetime.now(UTC)
    await _set(first, status="submitted", submitted_at=now - timedelta(hours=2))
    await _set(second, status="submitted", submitted_at=now - timedelta(hours=1), priority=9999)

    _, rev = await add_member(client, admin_token, pid, "reviewer")
    r = await client.post(f"/projects/{pid}/review/next", headers={"Authorization": f"Bearer {rev}"})
    assert r.status_code == 200, r.text
    assert r.json()["work_item_id"] == first
