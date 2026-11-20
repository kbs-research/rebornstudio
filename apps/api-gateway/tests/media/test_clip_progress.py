"""클립 진척 요약 테스트.

클립 행의 `progress`는 승인·진행 중·작업 전 세 값이고, 제외된 클립은 None이다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import WorkItem
from api_gateway.services.media.progress import clip_progress
from httpx import AsyncClient
from tests._helpers import add_member, new_project, seed_clip_work, upload_video


async def _set_status(work_item_id: str, status: str) -> None:
    """작업 티켓의 상태를 바로 바꾼다. 상태만 필요하므로 가져오기·제출·검수 흐름을 거치지 않는다."""
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, UUID(work_item_id))
        wi.status = status
        await s.commit()


async def _seed(client: AsyncClient, admin_token: str, name: str) -> tuple[str, str]:
    """승인 1·재작업 1·라벨링 대기 1·제외 1·비식별화 신고 1인 클립 다섯 개짜리 영상을 만든다."""
    pid = await new_project(client, admin_token, name)
    vid = await upload_video(client, admin_token, pid)
    _, w_ok = await seed_clip_work(pid, vid, start=0, end=5)
    await _set_status(w_ok, "approved")
    _, w_re = await seed_clip_work(pid, vid, start=5, end=10)
    await _set_status(w_re, "rejected")
    await seed_clip_work(pid, vid, start=10, end=15)
    _, w_ex = await seed_clip_work(pid, vid, start=15, end=20, rights_status="rejected")
    await _set_status(w_ex, "cancelled")
    _, w_pe = await seed_clip_work(pid, vid, start=20, end=25, rights_status="legal_review")
    # 신고로 멈춘 클립의 티켓을 종료로 둬 "아무도 작업하지 않음"을 흉내 낸다.
    await _set_status(w_pe, "cancelled")
    return pid, vid


def test_clip_progress_rule():
    """클립 한 건의 진척 규칙을 확인한다. 제외는 None, 승인은 approved, 가져간 뒤 승인 전은 in_progress다."""
    assert clip_progress("rejected", "approved") is None
    assert clip_progress("cleared", "approved") == "approved"
    for st in ("claimed", "submitted", "rejected"):
        assert clip_progress("cleared", st) == "in_progress", st
    assert clip_progress("cleared", "available") == "not_started"
    assert clip_progress("legal_review", None) == "not_started"


async def test_clip_rows_carry_progress(client: AsyncClient, admin_token: str):
    """클립 행의 `progress`가 작업 상태와 클립 상태에서 정한 값인지 확인한다. 제외된 클립은 None이다."""
    pid, vid = await _seed(client, admin_token, "prog-rows")
    h = {"Authorization": f"Bearer {admin_token}"}
    clips = (await client.get(f"/projects/{pid}/videos/{vid}/clips", headers=h)).json()
    got = [c["progress"] for c in clips]
    assert got == ["approved", "in_progress", "not_started", None, "not_started"]


async def test_project_clip_list_carries_work_status(client: AsyncClient, admin_token: str):
    """프로젝트 전체 클립 목록도 작업 상태를 싣는다. 클립 표의 작업 열이 이 값을 쓴다."""
    pid, _ = await _seed(client, admin_token, "prog-proj")
    rows = (await client.get(f"/projects/{pid}/clips",
                             headers={"Authorization": f"Bearer {admin_token}"})).json()
    by_start = {c["start_seconds"]: c for c in rows}
    assert by_start[0.0]["work_status"] == "approved"
    assert by_start[5.0]["work_status"] == "rejected"
    assert by_start[10.0]["work_status"] == "available"


async def test_admin_keeps_operational_state(client: AsyncClient, admin_token: str):
    """관리자·검수자에게는 운영 상태를 그대로 보여 준다."""
    pid, vid = await _seed(client, admin_token, "prog-admin")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    for role, tok in (("admin", admin_token), ("reviewer", rev)):
        clips = (await client.get(f"/projects/{pid}/videos/{vid}/clips",
                                  headers={"Authorization": f"Bearer {tok}"})).json()
        assert len(clips) == 5, role
        assert "rejected" in {c["work_status"] for c in clips}, role
