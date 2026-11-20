"""내보내기의 후보 행과 보류 목록이 같은 시점을 보는지 확인하는 테스트.

후보 행(`approved_rows_stmt`)과 품질검수 보류 목록(`qc_held_ids`)을 따로 읽는다.
읽기 사이에 다른 트랜잭션이 승인분을 보류로 바꾸면, 그 건이 패키지에 담기면서 제외 목록에도 실린다.
받는 쪽은 같은 건을 담긴 수와 제외 수에서 두 번 센다.
"""
from datetime import UTC, datetime
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import WorkItem
from api_gateway.services.exports import export_package, export_service
from httpx import AsyncClient
from sqlalchemy import update
from tests._helpers import new_project, seed_clip_work, upload_video


async def _approve(work_item_id: str) -> None:
    """작업을 승인 상태로 바꾼다. 후보 행에 들어가는 데 필요한 값만 채운다."""
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, UUID(work_item_id))
        wi.status = "approved"
        wi.submitted_at = datetime.now(UTC)
        await s.commit()


async def test_rows_and_held_list_come_from_one_snapshot(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """후보 행을 읽은 뒤 보류로 바뀐 건은 이번 패키지의 보류 목록에 실리지 않는다."""
    pid = await new_project(client, admin_token, "export-snapshot")
    vid = await upload_video(client, admin_token, pid)
    _c1, first = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, second = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    for wid in (first, second):
        await _approve(wid)

    real_held = export_service.qc_held_ids
    seen: dict = {}

    async def _held_after_concurrent_hold(db, project_id):
        """보류 목록을 읽기 직전에 다른 트랜잭션이 승인분 하나를 보류로 바꾼다."""
        if str(project_id) == pid:
            async with SessionLocal() as other:
                await other.execute(update(WorkItem).where(WorkItem.work_item_id == UUID(first))
                                    .values(qc_status="held"))
                await other.commit()
        return await real_held(db, project_id)

    def _capture(rows, approved_at, out, **kw):
        """패키지에 넘긴 후보 행과 보류 목록을 기록하고 조립을 멈춘다."""
        if rows and str(rows[0][0].project_id) == pid:
            seen["included"] = {str(w.work_item_id) for w, _v, _c in rows}
            seen["held"] = {str(h) for h in kw.get("qc_held") or []}
        raise RuntimeError("captured")

    monkeypatch.setattr(export_service, "qc_held_ids", _held_after_concurrent_hold)
    monkeypatch.setattr(export_package, "write_package", _capture)
    r = await client.post(f"/projects/{pid}/exports",
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code in (200, 201), r.text
    async with SessionLocal() as s:
        await export_service.process_pending(s, limit=10)

    assert seen, "이 프로젝트의 패키지 조립까지 가지 않았다"
    assert not seen["included"] & seen["held"], f"같은 건이 담기고 제외됐다: {seen}"
