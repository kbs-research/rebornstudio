"""라벨링 기능의 BE 테스트. 대상은 draft와 report-deident-miss다."""
from decimal import Decimal
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import Clip, WorkItem
from httpx import AsyncClient
from tests._helpers import add_member, new_project, upload_video


@pytest_asyncio.fixture(loop_scope="session")
async def claimed_clip_work(client: AsyncClient, admin_token: str):
    """프로젝트·영상·클립과 claim한 클립 work_item을 만들고 (pid, vid, wid)를 돌려준다."""
    pid = await new_project(client, admin_token, "lbl")
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        clip = Clip(
            video_id=UUID(vid), project_id=UUID(pid),
            start_seconds=Decimal("0.000"), end_seconds=Decimal("5.000"),
            rights_status="auto_cleared",
            auto_caption={"brief": "장면"},
            speakers=[{"pid": "1", "name": "화자1"}, {"pid": "2", "name": "화자2"}],
        )
        s.add(clip)
        await s.flush()
        s.add(WorkItem(
            video_id=UUID(vid), project_id=UUID(pid), clip_id=clip.clip_id,
            status="available",
        ))
        await s.commit()
    claim = await client.post(
        f"/projects/{pid}/work/next",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert claim.status_code == 200
    return pid, vid, claim.json()["work_item_id"]


async def test_draft_save_and_restore(client: AsyncClient, admin_token: str, claimed_clip_work):
    """PATCH로 draft를 저장하면 GET work 응답에서 draft가 복원되는지 확인한다."""
    _, _, wid = claimed_clip_work
    r = await client.patch(
        f"/work/{wid}/draft",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"payload": {"caption": {"brief": "작성 중"}}},
    )
    assert r.status_code == 200
    got = await client.get(f"/work/{wid}", headers={"Authorization": f"Bearer {admin_token}"})
    assert got.json()["draft"] == {"caption": {"brief": "작성 중"}}


async def test_report_deident_miss_sets_legal_review(
    client: AsyncClient, admin_token: str, claimed_clip_work
):
    """report-deident-miss가 204를 돌려주고 clip.rights_status를 'legal_review'로 바꾸는지 확인한다."""
    _, vid, wid = claimed_clip_work
    r = await client.post(
        f"/work/{wid}/report-deident-miss",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"timestamp_ms": 1500, "reason": "얼굴 노출"},
    )
    assert r.status_code == 204
    async with SessionLocal() as s:
        clip = (await s.execute(
            Clip.__table__.select().where(Clip.video_id == UUID(vid))
        )).first()
        assert clip.rights_status == "legal_review"


async def test_draft_403_for_non_claimant(client: AsyncClient, admin_token: str, claimed_clip_work):
    """다른 annotator가 남이 claim한 작업에 draft를 저장하면 403인지 확인한다."""
    pid, _, wid = claimed_clip_work
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.patch(
        f"/work/{wid}/draft",
        headers={"Authorization": f"Bearer {annot_tok}"},
        json={"payload": {}},
    )
    assert r.status_code == 403
