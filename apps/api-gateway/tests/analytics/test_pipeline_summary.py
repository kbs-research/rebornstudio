"""작업 단계 대기 건수 요약(GET /projects/{id}/pipeline/summary) 회귀 테스트.

요약은 세지 않아야 할 대상을 정확히 빼는 것이 중요하다.
소프트 삭제분과 영상 단위 MI 티켓이 섞이면 단계 배지가 조용히 잘못된 수를 보인다.
"""
from uuid import UUID, uuid4

from api_gateway.database import SessionLocal
from api_gateway.models import Clip, Video
from httpx import AsyncClient
from tests._helpers import add_member, new_project, upload_video

_FIELDS = {
    "deident_reports",
    "awaiting_analysis", "analyzing", "label_available", "review_submitted", "repeated_rejects",
    "completed_total", "preparing",
}


async def test_summary_shape(client: AsyncClient, admin_token: str):
    """규격 검증 전인 영상 1건을 올리면 분석 대기도 비식별화 신고도 0인지 확인한다."""
    pid = await new_project(client, admin_token, "psum")
    await upload_video(client, admin_token, pid, title="p1")
    r = await client.get(
        f"/projects/{pid}/pipeline/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body) == _FIELDS
    assert body["deident_reports"] == 0
    assert body["awaiting_analysis"] == 0


async def test_cleared_video_moves_to_awaiting_analysis(client: AsyncClient, admin_token: str):
    """규격 검증을 통과한 영상이 바로 분석 대기가 되는지 확인한다.

    이때 생기는 work_item은 영상 단위 MI 티켓이라 라벨링 대기로 세지 않는다.
    세면 등록한 영상 수만큼 라벨링 대기가 늘어난다.
    """
    pid = await new_project(client, admin_token, "psum-cleared")
    await upload_video(client, admin_token, pid, title="p2", policy="pre_cleared", probed=True)
    body = (
        await client.get(
            f"/projects/{pid}/pipeline/summary",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).json()
    assert body["awaiting_analysis"] == 1
    assert body["label_available"] == 0


async def test_transcoding_video_counts_as_preparing(client: AsyncClient, admin_token: str):
    """변환 중인 영상을 대기 수가 아니라 `preparing`으로 세는지 확인한다. 반입 화면의 `변환 중`과 같은 수다.

    등록 직후 변환·비식별화가 도는 동안 대기 수만 보이면 단계 화면이 `대기 영상 0`으로 남는다.
    그러면 등록한 영상이 사라진 것처럼 보인다.
    """
    pid = await new_project(client, admin_token, "psum-prep")
    hdr = {"Authorization": f"Bearer {admin_token}"}
    busy = await upload_video(client, admin_token, pid, title="busy", policy="pre_cleared")
    await upload_video(client, admin_token, pid, title="idle", policy="pre_cleared", probed=True)
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(busy))
        v.transcode_status = "running"
        await s.commit()

    body = (await client.get(f"/projects/{pid}/pipeline/summary", headers=hdr)).json()
    assert body["preparing"] == 1
    assert body["awaiting_analysis"] == 1
    buckets = (await client.get(f"/projects/{pid}/videos/buckets", headers=hdr)).json()
    assert body["preparing"] == buckets["preparing"]


async def test_summary_excludes_soft_deleted(client: AsyncClient, admin_token: str):
    """소프트 삭제한 영상이 어느 단계에도 남지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "psum-del")
    vid = await upload_video(client, admin_token, pid, title="p3", probed=True)
    await client.delete(f"/videos/{vid}", headers={"Authorization": f"Bearer {admin_token}"})
    body = (
        await client.get(
            f"/projects/{pid}/pipeline/summary",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).json()
    assert body["awaiting_analysis"] == 0


async def test_summary_403_for_annotator(client: AsyncClient, admin_token: str):
    """요약을 운영자와 관리자만 볼 수 있어 annotator에게 403인지 확인한다."""
    pid = await new_project(client, admin_token, "psum-perm")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    r = await client.get(
        f"/projects/{pid}/pipeline/summary",
        headers={"Authorization": f"Bearer {annot}"},
    )
    assert r.status_code == 403


async def test_upload_batch_id_round_trips(client: AsyncClient, admin_token: str):
    """반입 배치 id가 등록할 때 저장되고 목록 DTO로 그대로 돌아오는지 확인한다.

    다음 단계 필터가 이 값을 쓴다.
    """
    pid = await new_project(client, admin_token, "batch")
    # 묶음 id는 프로젝트마다 새로 만든다. 다른 프로젝트가 만든 묶음 id는 등록이 404로 거절한다.
    batch = str(uuid4())
    vid = (
        await client.post(
            f"/projects/{pid}/videos",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": "b1", "copyright_policy": "pre_cleared", "upload_batch_id": batch},
        )
    ).json()["video_id"]
    rows = (
        await client.get(
            f"/projects/{pid}/videos",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).json()
    found = next(v for v in rows if v["video_id"] == vid)
    assert found["upload_batch_id"] == batch


async def _clip(pid: str, vid: str, start: float, status: str) -> None:
    """클립 상태를 지정한 클립 하나를 넣는다. 비식별화 신고 수를 세는 테스트에 쓴다."""
    async with SessionLocal() as s:
        s.add(Clip(
            project_id=UUID(pid), video_id=UUID(vid),
            start_seconds=start, end_seconds=start + 5.0, rights_status=status,
        ))
        await s.commit()


async def test_deident_badge_and_list_count_the_same_clips(client: AsyncClient, admin_token: str):
    """단계 요약의 `deident_reports`와 비식별화 신고 목록의 `total`이 같은 수인지 확인한다.

    두 수는 서로 다른 엔드포인트가 센다. 삭제한 영상의 신고와 이미 처리한 클립을 넣어
    두 쿼리가 달라질 수 있는 경우를 함께 본다.
    """
    pid = await new_project(client, admin_token, "psum-deident")
    hdr = {"Authorization": f"Bearer {admin_token}"}
    live = await upload_video(client, admin_token, pid, title="live")
    gone = await upload_video(client, admin_token, pid, title="gone")
    await _clip(pid, live, 0.0, "legal_review")
    await _clip(pid, live, 5.0, "legal_review")
    await _clip(pid, live, 10.0, "auto_cleared")   # 처리가 끝난 클립은 어느 쪽도 세지 않는다
    await _clip(pid, live, 15.0, "rejected")
    await _clip(pid, gone, 0.0, "legal_review")
    assert (await client.delete(f"/videos/{gone}", headers=hdr)).status_code == 204

    stage = (await client.get(f"/projects/{pid}/pipeline/summary", headers=hdr)).json()
    listed = (await client.get(f"/projects/{pid}/deident-reports", headers=hdr)).json()
    assert stage["deident_reports"] == listed["total"] == 2, (stage, listed)


async def test_completed_total_matches_the_completed_list(client: AsyncClient, admin_token: str):
    """단계 표시줄의 완료 수가 완료 목록 머리의 수와 같고 품질검수 보류분은 세지 않는지 확인한다.

    `approved` 상태를 직접 세면 보류분이 섞여 완료 목록보다 크게 나온다.
    """
    from api_gateway.models import WorkItem
    from sqlalchemy import update
    from tests._helpers import seed_clip_work

    pid = await new_project(client, admin_token, "psum-done")
    vid = await upload_video(client, admin_token, pid, title="p-done")
    ids = [(await seed_clip_work(pid, vid, start=i * 10.0, end=i * 10.0 + 5))[1] for i in range(3)]
    async with SessionLocal() as s:
        await s.execute(
            update(WorkItem).where(WorkItem.work_item_id.in_([UUID(i) for i in ids]))
            .values(status="approved")
        )
        await s.execute(
            update(WorkItem).where(WorkItem.work_item_id == UUID(ids[0])).values(qc_status="held")
        )
        await s.commit()

    h = {"Authorization": f"Bearer {admin_token}"}
    body = (await client.get(f"/projects/{pid}/pipeline/summary", headers=h)).json()
    listed = (await client.get(f"/projects/{pid}/completed/summary", headers=h)).json()
    assert body["completed_total"] == 2
    assert body["completed_total"] == listed["total"]
