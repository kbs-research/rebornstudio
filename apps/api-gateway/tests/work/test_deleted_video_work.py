"""삭제된 영상의 작업이 사람 큐에서 빠지는지 보는 테스트.

영상 삭제는 soft delete다(`deleted_at`).
통계는 `Video.deleted_at.is_(None)`로 삭제된 영상을 거른다.
라벨링·검수 claim도 같은 조건으로 걸러야 한다(`_human_workable()`).
거르지 않으면 관리자가 영상을 지워도 그 영상의 클립 작업이 큐에 남고, 라벨러가 삭제된 영상을 받아 작업한다.
되돌릴 수 없는 액션(삭제)의 결과가 다른 화면에서 어긋나는 경우다.
"""
from uuid import UUID

from httpx import AsyncClient
from tests._helpers import add_member, new_project, seed_clip_work, upload_video


async def _delete_video(client: AsyncClient, token: str, vid: str) -> None:
    """영상을 삭제하고 204인지 확인한다."""
    r = await client.delete(f"/videos/{vid}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 204, r.text


async def test_labeling_claim_skips_deleted_video(client: AsyncClient, admin_token: str):
    """삭제된 영상의 클립 작업이 라벨링 큐에서 빠지는지 확인한다."""
    pid = await new_project(client, admin_token, "del-label")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    await _delete_video(client, admin_token, vid)

    r = await client.post(
        f"/projects/{pid}/work/next", headers={"Authorization": f"Bearer {annot}"}
    )
    assert r.status_code == 404, f"삭제된 영상의 작업이 나왔다 — {r.status_code} {r.text[:160]}"


async def test_review_claim_skips_deleted_video(client: AsyncClient, admin_token: str):
    """삭제된 영상의 제출분이 검수 큐에서도 빠지는지 확인한다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem

    pid = await new_project(client, admin_token, "del-review")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    labeler, _ = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid)
    _, wid = await seed_clip_work(pid, vid)
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, UUID(wid))
        wi.status = "submitted"
        # `claimed_by`를 반드시 채운다.
        # NULL이면 `claimed_by != user`가 SQL에서 NULL로 평가되어 행이 조용히 빠진다.
        # 그러면 테스트가 삭제 조건이 아닌 다른 이유로 통과한다.
        wi.claimed_by = UUID(labeler)
        await s.commit()
    await _delete_video(client, admin_token, vid)

    r = await client.post(
        f"/projects/{pid}/review/next", headers={"Authorization": f"Bearer {rev}"}
    )
    assert r.status_code == 404, f"삭제된 영상의 제출분이 나왔다 — {r.status_code} {r.text[:160]}"
