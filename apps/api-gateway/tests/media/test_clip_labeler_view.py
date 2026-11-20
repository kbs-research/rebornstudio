"""라벨러가 받는 클립 응답에서 클립 상태 구분과 다른 라벨러의 작업 상태를 가리는지 확인하는 테스트.

라벨러에게는 정상 상태 클립의 구간·자막만 보여 준다.
판정 전·법무 검토·제외 클립은 작업자에게 보이면 안 되는 구간이라 목록에서 뺀다.
작업 상태는 호출한 라벨러가 가져간 작업에만 남긴다. 다른 라벨러의 승인·반려는 그 라벨러의 기록이다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import WorkItem
from httpx import AsyncClient
from tests._helpers import add_member, new_project, seed_clip_work, upload_video


async def _set(work_item_id: str, status: str, claimed_by: str | None = None) -> None:
    """작업 티켓의 상태와 가져간 사람을 바로 바꾼다."""
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, UUID(work_item_id))
        wi.status = status
        wi.claimed_by = UUID(claimed_by) if claimed_by else None
        await s.commit()


async def _seed(client: AsyncClient, admin_token: str):
    """허가 클립 넷(호출자 작업·다른 라벨러의 승인·재작업·대기)과 비식별화 신고·제외 클립 하나씩을 만든다."""
    pid = await new_project(client, admin_token, "lb-view")
    vid = await upload_video(client, admin_token, pid)
    me_id, me = await add_member(client, admin_token, pid, "annotator")
    other_id, _ = await add_member(client, admin_token, pid, "annotator")
    _, w_mine = await seed_clip_work(pid, vid, start=0, end=5, rights_status="cleared")
    await _set(w_mine, "claimed", me_id)
    _, w_ok = await seed_clip_work(pid, vid, start=5, end=10, rights_status="cleared")
    await _set(w_ok, "approved", other_id)
    _, w_q = await seed_clip_work(pid, vid, start=10, end=15, rights_status="auto_cleared")
    await _set(w_q, "submitted", other_id)
    await seed_clip_work(pid, vid, start=15, end=20, rights_status="cleared")
    for i, rs in enumerate(("legal_review", "rejected")):
        _, w = await seed_clip_work(pid, vid, start=20 + 5 * i, end=25 + 5 * i, rights_status=rs)
        await _set(w, "cancelled")
    return pid, vid, me, w_mine


async def test_labeler_video_clips_hide_rights_and_others_work(client: AsyncClient, admin_token: str):
    """영상 클립 목록은 허가 클립만 싣고 판정 값을 하나로 합치며 작업 상태는 호출자 것만 남긴다."""
    pid, vid, me, w_mine = await _seed(client, admin_token)
    r = await client.get(f"/projects/{pid}/videos/{vid}/clips",
                         headers={"Authorization": f"Bearer {me}"})
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [c["start_seconds"] for c in rows] == [0.0, 5.0, 10.0, 15.0], "신고 중·제외 클립이 라벨러에게 나갔다"
    assert {c["rights_status"] for c in rows} == {"cleared"}, "라벨러에게 판정 구분이 나갔다"
    assert [c["work_status"] for c in rows] == ["claimed", None, None, None], "남의 작업 상태가 나갔다"
    assert rows[0]["work_item_id"] == w_mine
    assert all(c["work_item_id"] is None for c in rows[1:])
    assert [c["progress"] for c in rows] == ["in_progress", "not_started", "not_started", "not_started"]


async def test_labeler_project_clips_and_single_clip(client: AsyncClient, admin_token: str):
    """프로젝트 클립 목록과 단건 조회에도 같은 규칙을 적용한다. 허가 전 클립은 단건 조회도 404다."""
    pid, _, me, _ = await _seed(client, admin_token)
    h = {"Authorization": f"Bearer {me}"}
    rows = (await client.get(f"/projects/{pid}/clips", params={"limit": 200}, headers=h)).json()
    assert len(rows) == 4
    assert {c["rights_status"] for c in rows} == {"cleared"}
    count = (await client.get(f"/projects/{pid}/clips/count", headers=h)).json()
    assert count == {"total": 4}, "분모가 라벨러에게 보이지 않는 클립까지 셌다"
    assert sorted(c["work_status"] or "" for c in rows) == ["", "", "", "claimed"]

    async with SessionLocal() as s:
        from api_gateway.models import Clip
        from sqlalchemy import select
        hidden = await s.scalar(select(Clip.clip_id).where(
            Clip.project_id == UUID(pid), Clip.rights_status == "rejected"))
        shown = await s.scalar(select(Clip.clip_id).where(
            Clip.project_id == UUID(pid), Clip.rights_status == "auto_cleared"))
    assert (await client.get(f"/projects/{pid}/clips/{hidden}", headers=h)).status_code == 404
    one = (await client.get(f"/projects/{pid}/clips/{shown}", headers=h)).json()
    assert one["rights_status"] == "cleared" and one["work_status"] is None


async def test_reviewer_still_sees_everything(client: AsyncClient, admin_token: str):
    """검수자에게는 판정과 작업 상태를 그대로 보여 준다. 가림은 라벨러에게만 적용한다."""
    pid, vid, _, _ = await _seed(client, admin_token)
    _, op = await add_member(client, admin_token, pid, "reviewer")
    rows = (await client.get(f"/projects/{pid}/videos/{vid}/clips",
                             headers={"Authorization": f"Bearer {op}"})).json()
    assert len(rows) == 6
    assert {"legal_review", "rejected", "auto_cleared"} <= {c["rights_status"] for c in rows}
    assert {"approved", "submitted"} <= {c["work_status"] for c in rows}
