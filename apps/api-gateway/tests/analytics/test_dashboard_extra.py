"""workflow-health와 진척률 규칙 테스트.

진척률은 여러 화면에서 한 규칙으로 계산한다.
운영자 전용 화면의 권한 매트릭스를 검사한다.
"""
import uuid

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Project, WorkItem
from api_gateway.services.analytics import stats as stats_service
from httpx import AsyncClient
from sqlalchemy import func, select
from tests._helpers import add_member, make_user, new_project, seed_clip_work, upload_video


async def test_workflow_health_returns_queue_depth(client: AsyncClient, admin_token: str):
    """workflow-health가 queue_depth를 돌려주는지(project_admin) 확인한다."""
    pid = await new_project(client, admin_token, "wfh")
    r = await client.get(
        f"/projects/{pid}/dashboard/workflow-health",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"queue_depth"}


async def test_workflow_health_403_for_annotator(client: AsyncClient, admin_token: str):
    """workflow-health가 project_admin 전용이라 annotator에게 403인지 확인한다."""
    pid = await new_project(client, admin_token, "wfh-perm")
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.get(
        f"/projects/{pid}/dashboard/workflow-health",
        headers={"Authorization": f"Bearer {annot_tok}"},
    )
    assert r.status_code == 403


# ── 진척률 규칙, 오래된 미완 작업, 권한 매트릭스 ──────────────────────────────────────


async def _approve_one_of_two(client: AsyncClient, admin_token: str, prefix: str) -> str:
    """사람 작업 두 건 중 한 건을 승인한 프로젝트를 만든다. 영상 단위 티켓이 한 건 섞여 있다.

    업로드가 디스패치 표식(`clip_id IS NULL`)을 만들어 둔다.
    그래서 사람 작업만 세면 1/2이고 전부 세면 1/3이다.
    """
    pid = await new_project(client, admin_token, prefix)
    vid = await upload_video(client, admin_token, pid)
    # 한 시간씩 둔다. 시간 합이 소수 4자리 반올림에 영향을 받지 않는 크기다.
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=3600.0)
    await seed_clip_work(pid, vid, start=3600.0, end=7200.0)
    async with SessionLocal() as s:
        (await s.get(WorkItem, uuid.UUID(w1))).status = "approved"
        await s.commit()
        tickets = await s.scalar(
            select(func.count()).select_from(WorkItem).where(
                WorkItem.project_id == uuid.UUID(pid), WorkItem.clip_id.is_(None),
            )
        )
    assert tickets == 1, "티켓이 없으면 이 시험은 아무것도 안 가른다"
    stats_service.reset_cache()
    return pid


async def test_progress_is_one_rule_on_every_screen(client: AsyncClient, admin_token: str):
    """같은 프로젝트의 진척률이 두 응답에서 같은지 확인한다. 분모·반올림·계산 위치를 하나로 둔다.

    두 응답 모두 `stats.progress_pct` 한 함수로 계산한다.
    한 응답이라도 티켓을 분모에 넣거나 화면이 다시 나눠 반올림하면 같은 프로젝트의 진척률이 화면마다 다르다.
    """
    pid = await _approve_one_of_two(client, admin_token, "prog-one")
    h = {"Authorization": f"Bearer {admin_token}"}

    stats = (await client.get(f"/projects/{pid}/stats", headers=h)).json()
    kpi = (await client.get(f"/projects/{pid}/dashboard/business-kpi", headers=h)).json()

    # 분모는 사람 작업 두 건이다. 티켓은 승인될 수 없어서 분모에 넣으면 100%에 도달하지 못한다.
    assert (kpi["done_count"], kpi["total_count"]) == (1, 2)
    assert stats["total_work_items"] == 2
    # 두 값이 같다. 서버가 소수 1자리로 낸다.
    assert stats["progress_pct"] == kpi["progress_pct"] == 50.0


async def test_hours_progress_says_which_yardstick(client: AsyncClient, admin_token: str):
    """시간 진척을 기준(분모)과 함께 내는지 확인한다. 개요와 통계가 같은 값을 표시하게 한다.

    목표가 있으면 계약 목표가 기준이고, 없으면 반입 분량이 기준이다.
    클립이 없으면 `None`이고 화면은 건수로 표시한다. 0으로 나눈 %를 표시하지 않는다.
    """
    h = {"Authorization": f"Bearer {admin_token}"}
    # 클립이 없으면 시간 진척이 없다.
    empty = await new_project(client, admin_token, "hp-empty")
    body = (await client.get(f"/projects/{empty}/dashboard/business-kpi", headers=h)).json()
    assert body["hours_progress"] is None

    pid = await _approve_one_of_two(client, admin_token, "hp")   # 반입 2시간, 완료 1시간
    kpi = (await client.get(f"/projects/{pid}/dashboard/business-kpi", headers=h)).json()
    assert kpi["hours_progress"] == {"pct": 50.0, "basis": "intake", "scale_hours": 2.0}

    async with SessionLocal() as s:
        (await s.get(Project, uuid.UUID(pid))).target_hours = 10.0   # 목표 10시간은 반입 2시간보다 크다
        await s.commit()
    stats_service.reset_cache()
    kpi = (await client.get(f"/projects/{pid}/dashboard/business-kpi", headers=h)).json()
    assert kpi["hours_progress"] == {"pct": 10.0, "basis": "target", "scale_hours": 10.0}

    # 목표 0은 정하지 않았다는 뜻이다. 계약 목표를 기준으로 표시하면서 0으로 나눌 수는 없다.
    assert stats_service.hours_progress(1.0, 2.0, 0).basis == "intake"


# 관리자 전용 화면의 엔드포인트 목록.
# 권한 기준이 바뀌면 이 목록 전체가 한 번에 실패하도록 한곳에 모은다.
_ADMIN_ONLY = [
    "/projects/{pid}/qc/held",
    "/projects/{pid}/dashboard/capacity",
    "/projects/{pid}/dashboard/labeler-performance",
]


@pytest.fixture
async def access_matrix_project(client: AsyncClient, admin_token: str):
    """권한 매트릭스용 데이터(프로젝트와 라벨러·검수자·비멤버·프로젝트 관리자 토큰)를 만든다."""
    pid = await new_project(client, admin_token, "acl")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    _, reviewer = await add_member(client, admin_token, pid, "reviewer")
    _, outsider = await make_user(client, admin_token, "outsider")
    _, manager = await add_member(client, admin_token, pid, "project_admin")
    return pid, {
        "annotator": annot, "reviewer": reviewer, "outsider": outsider, "manager": manager,
    }


@pytest.mark.parametrize("path", _ADMIN_ONLY)
async def test_admin_surfaces_refuse_labelers_and_outsiders(
    client: AsyncClient, access_matrix_project, path: str
):
    """라벨러·검수자·비멤버는 403, 프로젝트 관리자는 200인지 확인한다.

    모든 화면에 같은 권한 기준을 적용한다.
    """
    pid, tok = access_matrix_project
    url = path.format(pid=pid)
    for who in ("annotator", "reviewer", "outsider"):
        r = await client.get(url, headers={"Authorization": f"Bearer {tok[who]}"})
        assert r.status_code == 403, f"{who} {url} → {r.status_code}"
    r = await client.get(url, headers={"Authorization": f"Bearer {tok['manager']}"})
    assert r.status_code == 200, f"manager {url} → {r.status_code} {r.text}"
