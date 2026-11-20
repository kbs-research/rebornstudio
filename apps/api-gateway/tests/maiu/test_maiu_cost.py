"""MAIU 비용 통계가 영상 길이로 비용을 예측하는지 확인하는 테스트(1시간 ≈ 1만원)."""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob
from httpx import AsyncClient
from sqlalchemy import text
from tests import _storage
from tests.conftest import uniq


async def _make_project_video(client: AsyncClient, h: dict) -> tuple[str, str]:
    """프로젝트와 영상 1건을 만들고 (project_id, video_id)를 돌려준다."""
    slug = f"cost-{uniq('').split('@')[0]}"
    pid = (await client.post(
        "/projects", headers=h, json={"slug": slug, "name": "cost"},
    )).json()["project_id"]
    await _storage.pin_project(pid)
    vid = (await client.post(
        f"/projects/{pid}/videos", headers=h,
        json={"title": "v", "copyright_policy": "pre_cleared"},
    )).json()["video_id"]
    return pid, vid


async def test_cost_stats_duration_based(client: AsyncClient, admin_token: str):
    """done 영상이 1시간이면 estimated_cost_krw는 1만원, processed_video_count는 1이다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid, vid = await _make_project_video(client, h)
    # 영상 길이를 1시간(3600s)으로 두고 done 상태의 MAIU job을 넣는다.
    async with SessionLocal() as s:
        await s.execute(
            text("UPDATE videos SET duration_seconds=3600 WHERE video_id=CAST(:v AS uuid)")
            .bindparams(v=vid)
        )
        s.add(AnalysisJob(adapter_key="maiu", video_id=UUID(vid), project_id=UUID(pid), status="done"))
        await s.commit()

    r = await client.get(f"/projects/{pid}/maiu/cost-stats", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["processed_video_count"] == 1
    assert body["total_video_hours"] == 1.0
    assert body["estimated_cost_krw"] == 10_000
    assert body["cost_per_hour_krw"] == 10_000


async def test_cost_stats_empty_is_zero(client: AsyncClient, admin_token: str):
    """처리된 영상(done job)이 없으면 비용은 0이다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid, _ = await _make_project_video(client, h)
    body = (await client.get(f"/projects/{pid}/maiu/cost-stats", headers=h)).json()
    assert body["processed_video_count"] == 0
    assert body["estimated_cost_krw"] == 0
    assert body["total_video_hours"] == 0.0


async def test_cost_stats_remaining_excludes_done_and_deleted(
    client: AsyncClient, admin_token: str
):
    """남은 분량은 아직 done이 아닌 영상만 센다. done과 삭제한 영상은 빠진다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid, done_vid = await _make_project_video(client, h)
    todo_vid = (await client.post(
        f"/projects/{pid}/videos", headers=h,
        json={"title": "todo", "copyright_policy": "pre_cleared"},
    )).json()["video_id"]
    dropped_vid = (await client.post(
        f"/projects/{pid}/videos", headers=h,
        json={"title": "dropped", "copyright_policy": "pre_cleared"},
    )).json()["video_id"]

    async with SessionLocal() as s:
        await s.execute(
            text(
                "UPDATE videos SET duration_seconds = CASE video_id"
                " WHEN CAST(:d AS uuid) THEN 3600"
                " WHEN CAST(:t AS uuid) THEN 7200"
                " ELSE 18000 END,"
                " deleted_at = CASE WHEN video_id = CAST(:x AS uuid)"
                " THEN now() ELSE deleted_at END"
                " WHERE video_id IN (CAST(:d AS uuid), CAST(:t AS uuid), CAST(:x AS uuid))"
            ).bindparams(d=done_vid, t=todo_vid, x=dropped_vid)
        )
        s.add(AnalysisJob(adapter_key="maiu", video_id=UUID(done_vid), project_id=UUID(pid), status="done"))
        await s.commit()

    body = (await client.get(f"/projects/{pid}/maiu/cost-stats", headers=h)).json()
    assert body["total_video_hours"] == 1.0        # done 1시간만 지출
    assert body["remaining_video_hours"] == 2.0    # 5시간 영상은 삭제해서 세지 않는다
    assert body["remaining_cost_krw"] == 20_000
    assert body["remaining_unmeasured_count"] == 0

    # 남은 분량은 현재 재고라 `month`와 무관하다. 지출과 호출만 그 달로 한정한다.
    scoped = (await client.get(
        f"/projects/{pid}/maiu/cost-stats?month=1999-01", headers=h
    )).json()
    assert scoped["remaining_video_hours"] == 2.0
    assert scoped["total_video_hours"] == 0.0


async def test_cost_stats_remaining_reports_unmeasured(
    client: AsyncClient, admin_token: str
):
    """길이가 없는 영상은 남은 시간 합계에서 빼고 편수를 따로 돌려준다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid, _ = await _make_project_video(client, h)
    body = (await client.get(f"/projects/{pid}/maiu/cost-stats", headers=h)).json()
    assert body["remaining_video_hours"] == 0.0
    assert body["remaining_unmeasured_count"] == 1


async def test_cost_stats_rejects_bad_month(client: AsyncClient, admin_token: str):
    """`month`는 YYYY-MM 형식만 받는다. 형식이 틀린 값을 전 기간으로 처리하지 않고 422로 거절한다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid, _ = await _make_project_video(client, h)
    r = await client.get(f"/projects/{pid}/maiu/cost-stats?month=2026-13-01", headers=h)
    assert r.status_code == 422
