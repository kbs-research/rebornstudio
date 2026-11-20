"""대시보드 엔드포인트(labeler-performance, business-kpi 등) 테스트."""
import pytest
from httpx import AsyncClient
from tests._helpers import (
    add_member,
    caption_payload,
    claim_review,
    invite_user,
    new_project,
    seed_clip_work,
    upload_video,
)


async def test_labeler_performance_lists_members(client: AsyncClient, admin_token: str):
    """labeler-performance가 활성 멤버 행(관리자 본인 포함)을 돌려주는지 확인한다."""
    pid = await new_project(client, admin_token, "dash-perf")
    await add_member(client, admin_token, pid, "annotator")
    r = await client.get(
        f"/projects/{pid}/dashboard/labeler-performance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    assert set(rows[0]) >= {"user_id", "submitted", "approval_rate", "rejection_rate"}


async def test_labeler_performance_credits_the_labeler_not_the_reviewer(
    client: AsyncClient, admin_token: str
):
    """통과율·반려율이 이 사람이 내린 판정이 아니라 이 사람의 라벨이 받은 판정을 세는지 확인한다.

    `review.*` 이벤트를 actor 기준으로 세면 actor가 검수자라서 주체가 뒤바뀐다.
    그러면 라벨러 성과 표가 이 사람이 검수자로서 반려한 건수를 보여 주고, 검수만 하는 사람에게 처리량 0인데 반려율이 붙는다.
    라벨러 2건(승인 1, 반려 1)과 검수자 1명으로 이 뒤바뀜을 잡는다.
    """
    pid = await new_project(client, admin_token, "perf-attr")
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    rid, rev_tok = await add_member(client, admin_token, pid, "reviewer")

    for decision in ("approve", "reject"):
        vid = await upload_video(client, admin_token, pid, title=f"v-{decision}")
        await seed_clip_work(pid, vid)
        wid = (await client.post(
            f"/projects/{pid}/work/next", headers={"Authorization": f"Bearer {annot_tok}"}
        )).json()["work_item_id"]
        await client.post(
            f"/work/{wid}/submit", headers={"Authorization": f"Bearer {annot_tok}"},
            json={"payload": caption_payload("현황")},
        )
        await claim_review(client, rev_tok, pid, wid)
        await client.post(
            f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev_tok}"},
            json={"decision": decision, "reason": "다시" if decision == "reject" else None},
        )

    rows = (await client.get(
        f"/projects/{pid}/dashboard/labeler-performance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    by_id = {r["user_id"]: r for r in rows}

    labeler = next(r for r in rows if r["submitted"] == 2)
    assert (labeler["approved"], labeler["rejected"]) == (1, 1), labeler
    assert labeler["approval_rate"] == 50.0 and labeler["rejection_rate"] == 0.5, labeler

    # 검수자에게는 아무 값도 붙지 않는다. 라벨을 낸 적이 없으므로 분모가 없다.
    reviewer = by_id[str(rid)]
    assert (reviewer["submitted"], reviewer["approved"], reviewer["rejected"]) == (0, 0, 0), reviewer
    assert reviewer["rejection_rate"] is None, reviewer


async def test_labeler_performance_counts_labels_not_events(
    client: AsyncClient, admin_token: str
):
    """제출 수가 이벤트가 아니라 라벨 행을 세는지 확인한다. 이벤트를 남기지 않는 경로가 있다.

    시드와 수기 전이는 `Review`·`Label` 행만 만들고 `domain_events`를 남기지 않는다.
    이벤트로 세면 라벨러가 제출했는데도 성과 표의 모든 행이 0이 된다.
    """
    pid = await new_project(client, admin_token, "perf-src")
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (await client.post(
        f"/projects/{pid}/work/next", headers={"Authorization": f"Bearer {annot_tok}"}
    )).json()["work_item_id"]
    await client.post(
        f"/work/{wid}/submit", headers={"Authorization": f"Bearer {annot_tok}"},
        json={"payload": caption_payload("현황")},
    )

    rows = (await client.get(
        f"/projects/{pid}/dashboard/labeler-performance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert any(r["submitted"] == 1 for r in rows), rows
    submitter = next(r for r in rows if r["submitted"] == 1)
    # 아직 검수 전이다. 반려율의 분모는 작업량(제출)이라서 값이 있다.
    # 낸 1건 중 반려는 0건이므로 `0.0`이 맞다.
    # 통과율의 분모는 판정이 끝난 건이라 0이므로 통과율은 `None`이다.
    # 아래 두 assert가 두 비율의 분모가 다르다는 것을 확인한다.
    assert submitter["rejection_rate"] == 0.0, submitter
    assert submitter["approval_rate"] is None, submitter


async def test_labeler_performance_includes_quality(client: AsyncClient, admin_token: str):
    """labeler-performance가 작업량과 반려만 보여 주고 품질 등급 필드는 없는지 확인한다.

    품질 등급·점수·허니팟은 응답에 없다.
    반려율의 분모는 작업량(제출)이고, 낸 것이 0건이면 0%가 아니라 None이다.
    통과율은 분모(판정이 끝난 건)가 달라서 `None`이 되는 조건도 다르다.
    """
    pid = await new_project(client, admin_token, "dash-q")
    await add_member(client, admin_token, pid, "annotator")
    rows = (await client.get(
        f"/projects/{pid}/dashboard/labeler-performance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert all("rejection_rate" in r for r in rows)
    # 등급 필드는 계약에 없다. 다시 생기면 화면이 등급을 표시한다.
    assert all("standing" not in r and "score" not in r for r in rows)
    # 낸 것이 없는 멤버는 반려율이 None이다. 값을 만들어 내지 않는다.
    # 기준은 검수 이력이 아니라 제출이다. 분모가 작업량이라 조건도 작업량을 따른다.
    for r in rows:
        if r["submitted"] == 0:
            assert r["rejection_rate"] is None
        if r["approved"] + r["rejected"] == 0:
            assert r["approval_rate"] is None


async def test_business_kpi_reviewer_allowed(client: AsyncClient, admin_token: str):
    """business-kpi가 검수자에게도 200인지 확인한다."""
    pid = await new_project(client, admin_token, "dash-kpi")
    _, rev_tok = await add_member(client, admin_token, pid, "reviewer")
    r = await client.get(
        f"/projects/{pid}/dashboard/business-kpi",
        headers={"Authorization": f"Bearer {rev_tok}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"progress_pct", "done_count", "total_count"}


async def test_business_kpi_ops_state_only_for_admin(client: AsyncClient, admin_token: str):
    """납품 가능 요약이 관리자 응답에만 실리는지 확인한다.

    납품 요약은 비용이 큰 전수 검사라 검수자에게는 싣지 않는다.
    개요가 읽지 않는 최근 활동 필드는 응답에 없다.
    """
    pid = await new_project(client, admin_token, "dash-kpi-mask")
    _, rev_tok = await add_member(client, admin_token, pid, "reviewer")

    def get(tok: str):
        """이 토큰으로 business-kpi를 조회한다."""
        return client.get(
            f"/projects/{pid}/dashboard/business-kpi",
            headers={"Authorization": f"Bearer {tok}"},
        )

    admin = (await get(admin_token)).json()
    reviewer = (await get(rev_tok)).json()
    assert admin["deliverable"] == {"count": 0}
    assert reviewer["deliverable"] is None
    assert "recent_activity" not in admin and "recent_activity" not in reviewer


async def test_business_kpi_403_for_outsider(client: AsyncClient, admin_token: str):
    """business-kpi가 비멤버에게 403인지 확인한다."""
    from tests._helpers import make_user
    pid = await new_project(client, admin_token, "dash-kpi-out")
    _, out_tok = await make_user(client, admin_token, "dash-out")
    r = await client.get(
        f"/projects/{pid}/dashboard/business-kpi",
        headers={"Authorization": f"Bearer {out_tok}"},
    )
    assert r.status_code == 403


async def test_labeler_performance_counts_only_this_project(
    client: AsyncClient, admin_token: str
):
    """라벨러 성과의 수를 이 프로젝트 안에서만 세는지 확인한다.

    프로젝트 필터 없이 세면 프로젝트 A의 표가 그 사람의 전체 프로젝트 합계를 보여 준다.
    멤버 목록은 프로젝트로 거르므로, 수를 거르지 않으면 같은 화면 안에서 분모가 서로 달라진다.
    """
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import Label, WorkItem
    from sqlalchemy import select

    pid_a = await new_project(client, admin_token, "perf-scope-a")
    pid_b = await new_project(client, admin_token, "perf-scope-b")
    # 양쪽 프로젝트의 멤버로 둔다.
    # 한쪽에만 두면 행이 없어서 통과하므로 다른 프로젝트의 수가 섞이는지 확인할 수 없다.
    uid, annot_tok = await add_member(client, admin_token, pid_a, "annotator")
    await invite_user(client, admin_token, pid_b, uid, "annotator")

    now = datetime.now(UTC)
    # 제출 수의 출처는 라벨 행이다. 이벤트를 남기지 않는 경로가 있기 때문이다.
    # 영상 단위 work는 영상당 하나라서(`uq_work_item_video_only`) 건마다 영상을 따로 둔다.
    vid_a1 = await upload_video(client, admin_token, pid_a, title="a1")
    vid_a2 = await upload_video(client, admin_token, pid_a, title="a2")
    vid_b = await upload_video(client, admin_token, pid_b, title="b")

    async def add_label(pid: str, vid: str, when) -> None:
        """업로드가 이미 만든 영상 단위 work에 라벨을 붙인다.

        영상 단위 work는 영상당 하나다(`uq_work_item_video_only`).
        """
        async with SessionLocal() as s:
            w = (await s.scalars(
                select(WorkItem).where(WorkItem.video_id == UUID(vid))
            )).one()
            w.claimed_by = UUID(uid)
            s.add(Label(work_item_id=w.work_item_id, submitted_by=UUID(uid),
                        label_data={}, submitted_at=when))
            await s.commit()

    # A에서 2건, B에서 1건이다. B의 건이 A의 표에 섞이면 안 된다.
    await add_label(pid_a, vid_a1, now - timedelta(days=10))
    await add_label(pid_a, vid_a2, now - timedelta(hours=1))
    await add_label(pid_b, vid_b, now - timedelta(hours=1))

    async def submitted_for(pid: str, **params) -> int:
        """이 프로젝트의 labeler-performance에서 admin의 제출 수를 읽는다."""
        r = await client.get(
            f"/projects/{pid}/dashboard/labeler-performance",
            headers={"Authorization": f"Bearer {admin_token}"},
            params=params,
        )
        assert r.status_code == 200, r.text
        row = next(x for x in r.json() if x["user_id"] == uid)
        return row["submitted"]

    # 전체 기간에서 A는 2건이다. B의 1건이 섞이면 3이 된다.
    assert await submitted_for(pid_a) == 2
    # B에는 A의 2건이 섞이면 안 된다.
    assert await submitted_for(pid_b) == 1


async def test_labeler_performance_since_narrows_window(
    client: AsyncClient, admin_token: str
):
    """`since`가 그 시각 이후만 세고 없으면 전체 기간을 누적하는지 확인한다.

    `최근 활동`에는 기간 필터를 적용하지 않는다.
    창 밖이어도 마지막으로 활동한 시각은 사실이고, 비워 두면 활동이 없었던 것으로 오해된다.
    """
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import DomainEvent, Label, WorkItem
    from sqlalchemy import select

    pid = await new_project(client, admin_token, "perf-since")
    uid, _ = await add_member(client, admin_token, pid, "annotator")

    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    # 영상 단위 work는 영상당 하나라서(`uq_work_item_video_only`) 건마다 영상을 따로 둔다.
    vids = [await upload_video(client, admin_token, pid, title=f"v{i}") for i in (1, 2)]
    async with SessionLocal() as s:
        # 제출 두 건 중 하나는 창 밖, 하나는 창 안에 둔다. 수의 출처는 라벨 행이다.
        for vid, when in zip(vids, (old, now - timedelta(hours=2)), strict=True):
            w = (await s.scalars(
                select(WorkItem).where(WorkItem.video_id == UUID(vid))
            )).one()
            w.claimed_by = UUID(uid)
            s.add(Label(work_item_id=w.work_item_id, submitted_by=UUID(uid),
                        label_data={}, submitted_at=when))
        # `최근 활동`은 이벤트에서 읽는다.
        # 창 필터가 적용되지 않는지가 이 테스트의 핵심이라서 창 밖 시각으로 이벤트를 하나 남긴다.
        s.add(DomainEvent(actor_user_id=UUID(uid), project_id=UUID(pid),
                          event_type="work.submitted", target_kind="work_item",
                          occurred_at=old))
        await s.commit()

    async def row_for(**params) -> dict:
        """labeler-performance에서 이 테스트 멤버의 행을 읽는다."""
        r = await client.get(
            f"/projects/{pid}/dashboard/labeler-performance",
            headers={"Authorization": f"Bearer {admin_token}"},
            params=params,
        )
        assert r.status_code == 200, r.text
        return next(x for x in r.json() if x["user_id"] == uid)

    assert (await row_for())["submitted"] == 2                       # 전체 기간
    cut = (now - timedelta(days=7)).isoformat()
    narrowed = await row_for(since=cut)
    assert narrowed["submitted"] == 1                                # 7일 창
    # 창을 좁혀도 최근 활동은 남는다
    assert narrowed["last_activity"] is not None


async def test_queue_depth_has_no_rejected_status(
    client: AsyncClient, admin_token: str
):
    """큐 깊이에 생기지 않는 `rejected` 상태를 만들지 않는지 확인한다.

    반려 처리는 work를 `available`·`claimed`로 되돌린다(`work/review.py`).
    그래서 `work_by_status["rejected"]`는 구조적으로 항상 0이다.
    항상 같은 값인 항목은 반려가 없다는 잘못된 정보를 보여 준다.
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem

    pid = await new_project(client, admin_token, "funnel")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=10.0, end=20.0)

    async with SessionLocal() as s:
        # 반려되어 대기로 돌아온 작업과 원 작업자가 다시 보유한 작업이다.
        a = await s.get(WorkItem, w1)
        a.rejection_count, a.status = 1, "available"
        b = await s.get(WorkItem, w2)
        b.rejection_count, b.status = 3, "claimed"
        await s.commit()

    health = (await client.get(
        f"/projects/{pid}/dashboard/workflow-health",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert "rejected" not in health["queue_depth"]
    assert set(health) == {"queue_depth"}, "화면이 읽지 않는 값은 응답에 싣지 않는다"
    assert health["queue_depth"].get("claimed") == 1

    kpi = (await client.get(
        f"/projects/{pid}/dashboard/business-kpi",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert "funnel" not in kpi and "quality" not in kpi


async def test_kpi_reports_hours_with_both_denominators(client: AsyncClient, admin_token: str):
    """KPI가 시간 단위 진척을 두 분모와 함께 내는지 확인한다.

    계약 단위가 시간이라서 건수만으로는 진척 보고에 쓸 수 없다.
    두 분모는 서로 다른 질문의 기준이다.
    `total_hours`는 가진 분량 중 처리한 비율, `target_hours`는 계약 대비 진척의 기준이다.
    하나만 주면 화면이 그 값으로 다른 질문까지 계산하므로 둘 다 준다.
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import Project, WorkItem

    pid = await new_project(client, admin_token, "hours")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=3600.0)     # 1시간
    _c2, _w2 = await seed_clip_work(pid, vid, start=3600.0, end=5400.0)  # 30분

    async with SessionLocal() as s:
        (await s.get(WorkItem, w1)).status = "approved"
        (await s.get(Project, __import__("uuid").UUID(pid))).target_hours = 10.0
        await s.commit()

    body = (await client.get(
        f"/projects/{pid}/dashboard/business-kpi",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert body["done_hours"] == 1.0        # 승인된 클립의 길이 합
    assert body["total_hours"] == 1.5       # 들어와 있는 분량
    assert body["target_hours"] == 10.0     # 계약 목표다. 위 두 값과 다른 수다.


async def test_hours_and_counts_are_not_each_others_denominator(
    client: AsyncClient, admin_token: str
):
    """건수와 시간이 서로의 분모가 아닌지 확인한다. 클립 길이가 제각각이라 건수와 시간은 비례하지 않는다.

    그래서 화면은 둘을 다른 수로 표시해야 한다.
    이 테스트는 길이가 다른 클립 두 개로 확인한다. 건수는 1:1인데 시간은 6:1이다.
    영상 단위 티켓은 사람 작업이 아니라 디스패치 표식이라 양쪽 모두에서 빠진다(`_human_workable`).
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem
    from api_gateway.services.analytics import stats as stats_service
    from sqlalchemy import select

    pid = await new_project(client, admin_token, "hours2")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=1800.0)      # 30분
    _c2, _w2 = await seed_clip_work(pid, vid, start=1800.0, end=2100.0)  # 5분

    async with SessionLocal() as s:
        (await s.get(WorkItem, w1)).status = "approved"
        await s.commit()

    async with SessionLocal() as s:
        hours = await stats_service.work_hours_by_status(s, __import__("uuid").UUID(pid))
    body = (await client.get(
        f"/projects/{pid}/stats",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()

    assert hours["approved"] == 0.5
    # 건수는 1:1
    assert body["work_by_status"].get("approved", 0) == 1
    assert body["work_by_status"].get("available", 0) == 1
    # 시간은 6:1이다. 건수는 같은데 진척이 다르다.
    assert body["work_hours_by_status"].get("available", 0.0) == pytest.approx(300 / 3600, abs=1e-3)

    # 영상 단위 표식은 업로드가 만들어 두는 분석 대기 표시라서 어느 수에서도 빠진다.
    async with SessionLocal() as s:
        markers = (await s.execute(
            select(WorkItem).where(
                WorkItem.project_id == __import__("uuid").UUID(pid), WorkItem.clip_id.is_(None),
            )
        )).scalars().all()
    assert len(markers) == 1, "표식이 없으면 아래 단언이 무의미하다"
    assert sum(body["work_by_status"].values()) == 2, "표식이 건수에 섞였다"


async def test_kpi_says_how_many_work_items_have_no_measurable_hours(
    client: AsyncClient, admin_token: str
):
    """클립이 없는 work의 수를 함께 내는지 확인한다. 이런 work는 시간 진척의 분모에서 빠진다.

    수를 주지 않으면 시간 진척이 조용히 낮게 나오고, 화면은 그 사실을 표시할 방법이 없다.
    """
    pid = await new_project(client, admin_token, "unmeas")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid, start=0.0, end=600.0)

    body = (await client.get(
        f"/projects/{pid}/dashboard/business-kpi",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    # 영상 단위 티켓(클립 없음)이 있으면 그만큼 잡힌다.
    assert body["hours_unmeasurable_count"] >= 1


async def test_capacity_stale_says_what_the_clock_is(client: AsyncClient, admin_token: str):
    """장기 체류 항목이 기준 시각(clock)을 함께 주는지 확인한다. `3일 넘음 4건`만 쓰면 무엇이 3일인지 알 수 없다."""
    pid = await new_project(client, admin_token, "cap2")
    body = (await client.get(
        f"/projects/{pid}/dashboard/capacity",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    by_key = {s["key"]: s for s in body["stale"]}
    assert by_key["review_wait"]["clock"] == "submitted_at"
    assert by_key["review_wait"]["threshold_days"] == 3
    assert by_key["untouched"]["clock"] == "created_at"


async def test_capacity_has_no_queue_runway(client: AsyncClient, admin_token: str):
    """처리 능력 응답에 큐 소진 예상 필드가 없는지 확인한다.

    읽는 화면이 없는 필드가 남으면 쓰임새를 다시 찾아야 하고 응답만 무거워진다.
    """
    pid = await new_project(client, admin_token, "cap3")
    body = (await client.get(
        f"/projects/{pid}/dashboard/capacity",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    for gone in (
        "queue_hours", "throughput_hours_per_day", "throughput_window_days",
        "throughput_window_start", "throughput_window_end", "queue_days_left",
    ):
        assert gone not in body, gone
    assert "stale" in body


async def test_stale_untouched_counts_human_work_only(client: AsyncClient, admin_token: str):
    """장기 미착수가 사람 작업만 세는지 확인한다.

    분석을 기다리는 영상 단위 티켓은 분석 대기 표시라서 세지 않는다.
    현황 탭의 대기 건수와 같은 모집단이어야 같은 `미착수`가 탭마다 다른 수로 나오지 않는다.
    """
    import uuid
    from datetime import UTC, datetime, timedelta

    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem
    from sqlalchemy import func, select, update

    pid = await new_project(client, admin_token, "stale-human")
    vid = await upload_video(client, admin_token, pid)      # 영상 단위 티켓 1건(클립 없음)
    await seed_clip_work(pid, vid)                            # 사람 작업 1건
    old = datetime.now(UTC) - timedelta(days=13)
    async with SessionLocal() as s:
        await s.execute(
            update(WorkItem).where(WorkItem.project_id == uuid.UUID(pid)).values(created_at=old)
        )
        await s.commit()
        tickets = await s.scalar(
            select(func.count()).select_from(WorkItem)
            .where(WorkItem.project_id == uuid.UUID(pid), WorkItem.clip_id.is_(None))
        )
    assert tickets >= 1, "업로드가 영상 단위 티켓을 만들어야 이 테스트가 뜻이 있다"

    body = (await client.get(
        f"/projects/{pid}/dashboard/capacity",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    by_key = {b["key"]: b for b in body["stale"]}
    assert by_key["untouched"]["count"] == 1
    assert by_key["untouched"]["label"] == "장기 미착수"
    assert by_key["review_wait"]["label"] == "장기 검수 대기"


async def test_labeler_performance_includes_nonmember_submitters(
    client: AsyncClient, admin_token: str
):
    """멤버가 아닌 제출자도 표에 행이 있고 검수자 리비전은 제출로 세지 않는지 확인한다.

    멤버가 아닌 사람의 제출이 빠지면 표의 합이 프로젝트의 제출 수보다 작아진다(역할 `None`).
    검수자가 수정 후 승인으로 넣은 리비전을 세면 검수자가 제출한 것처럼 보인다.
    """
    import uuid
    from datetime import UTC, datetime

    from api_gateway.database import SessionLocal
    from api_gateway.models import Label, Review
    from tests._helpers import make_user

    pid = await new_project(client, admin_token, "one-pop")
    annot_uid, _ = await add_member(client, admin_token, pid, "annotator")
    rev_uid, _ = await add_member(client, admin_token, pid, "reviewer")
    outsider_uid, _ = await make_user(client, admin_token, "one-pop-out")
    now = datetime.now(UTC)
    wids = []
    for i in range(3):
        vid = await upload_video(client, admin_token, pid, title=f"p{i}")
        _c, wid = await seed_clip_work(pid, vid)
        wids.append(uuid.UUID(wid))
    async with SessionLocal() as s:
        s.add(Label(work_item_id=wids[0], submitted_by=uuid.UUID(annot_uid),
                    label_data={}, submitted_at=now))
        s.add(Label(work_item_id=wids[1], submitted_by=uuid.UUID(outsider_uid),
                    label_data={}, submitted_at=now))
        # 수정 후 승인이다. 그 판정이 검수자가 쓴 리비전을 가리킨다.
        first = Label(work_item_id=wids[2], submitted_by=uuid.UUID(annot_uid),
                      label_data={}, submitted_at=now)
        s.add(first)
        await s.flush()
        revision = Label(work_item_id=wids[2], submitted_by=uuid.UUID(rev_uid),
                         label_data={}, submitted_at=now, parent_label_id=first.label_id)
        s.add(revision)
        await s.flush()
        s.add(Review(work_item_id=wids[2], reviewer_id=uuid.UUID(rev_uid),
                     verdict="modify_and_approve", label_id=revision.label_id, reviewed_at=now))
        await s.commit()

    h = {"Authorization": f"Bearer {admin_token}"}
    perf = (await client.get(f"/projects/{pid}/dashboard/labeler-performance", headers=h)).json()

    perf_total = {r["user_id"]: r["submitted"] for r in perf if r["submitted"]}
    assert perf_total == {annot_uid: 2, outsider_uid: 1}
    outsider = next(r for r in perf if r["user_id"] == outsider_uid)
    assert outsider["role"] is None
    assert next(r for r in perf if r["user_id"] == rev_uid)["submitted"] == 0


async def test_blocker_reasons_group_by_first_reason_only(
    client: AsyncClient, admin_token: str
):
    """사유가 여럿이어도 첫 사유로만 세는지 확인한다. 한 클립이 여러 항목에 잡히면 합이 건수보다 커진다."""
    pid = await new_project(client, admin_token, "cap5")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    await _approve_short(wid)     # 캡션 기준 미달인 채로 승인한다. 이 완료본은 납품에서 빠진다.

    body = (await client.get(
        f"/projects/{pid}/dashboard/capacity",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    # 키는 사유의 화면 이름이다. 코드(`caption 1`)를 힌트에 표시하지 않는다.
    assert body["blocker_reasons"] == {"캡션": 1}
    assert body["blocker_scan_limit"] > 0


async def test_blocker_reasons_is_none_not_empty_when_over_the_limit(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """스캔 상한을 넘으면 0건이 아니라 `None`인지 확인한다. `deliverable_total`과 같은 규칙이다."""
    from api_gateway.services.exports import export_service

    pid = await new_project(client, admin_token, "cap6")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    await _approve_short(wid)

    monkeypatch.setattr(export_service, "DELIVERABLE_SCAN_LIMIT", 0)
    body = (await client.get(
        f"/projects/{pid}/dashboard/capacity",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert body["blocker_reasons"] is None


async def _approve_short(work_item_id) -> None:
    """캡션이 짧은 채로 승인해서 납품 관문에 걸리는 완료본을 만든다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem

    async with SessionLocal() as s:
        w = await s.get(WorkItem, work_item_id)
        w.status = "approved"
        w.payload = {"caption": {"brief": "짧다"}}
        await s.commit()


async def test_labeler_row_carries_load_in_one_place(
    client: AsyncClient, admin_token: str
):
    """한 사람을 판단하는 데이터(작업량·부하)가 한 행에 모이는지 확인한다.

    작업량과 부하를 화면이 `user_id`로 조인할 수도 있지만 커서 페이지가 어긋나면 결과가 틀린다.
    그래서 서버가 한 행으로 모은다.
    """
    from datetime import UTC, datetime, timedelta

    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem

    pid = await new_project(client, admin_token, "people")
    vid = await upload_video(client, admin_token, pid)
    uid, _tok = await add_member(client, admin_token, pid, "annotator")
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=120.0)   # 2분짜리
    _c2, w2 = await seed_clip_work(pid, vid, start=120.0, end=240.0)

    now = datetime.now(UTC)
    async with SessionLocal() as s:
        done = await s.get(WorkItem, w1)
        done.claimed_by = __import__("uuid").UUID(uid)
        done.claimed_at, done.submitted_at = now - timedelta(seconds=60), now
        done.status = "submitted"
        held = await s.get(WorkItem, w2)          # 지금 가지고 있는 작업
        held.claimed_by, held.status = __import__("uuid").UUID(uid), "claimed"
        held.claimed_at = now
        await s.commit()

    rows = (await client.get(
        f"/projects/{pid}/dashboard/labeler-performance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    row = next(r for r in rows if r["user_id"] == uid)
    assert row["in_flight"] == 1
    # 동시 보유 한도가 없으므로 분모를 싣지 않는다.
    assert "max_concurrent" not in row


async def test_load_counts_review_by_role(
    client: AsyncClient, admin_token: str
):
    """현재 보유 수를 역할 도메인별로 세는지 확인한다.

    검수자는 review_claimed를 센다. 라벨링 claim만 세면 검수자가 이 표에 나와도 항상 0으로 보인다.
    """
    import uuid
    from datetime import UTC, datetime

    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem

    pid = await new_project(client, admin_token, "people-load")
    vid = await upload_video(client, admin_token, pid)
    rev_uid, _ = await add_member(client, admin_token, pid, "reviewer")

    _c, w = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    now = datetime.now(UTC)
    async with SessionLocal() as s:
        it = await s.get(WorkItem, uuid.UUID(w))
        it.status = "submitted"                       # 검수 대기 상태에서 review claim
        it.review_claimed_by = uuid.UUID(rev_uid)
        it.review_claimed_at = now
        await s.commit()

    rows = (await client.get(
        f"/projects/{pid}/dashboard/labeler-performance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    rev = next(r for r in rows if r["user_id"] == rev_uid)
    # 라벨링 claim만 세면 0이 된다.
    assert rev["in_flight"] == 1


async def test_old_expiry_does_not_end_a_review_hold(
    client: AsyncClient, admin_token: str
):
    """이전 버전이 남긴 지난 만료 시각이 있어도 검수 보유를 그대로 세는지 확인한다. 보유에는 끝나는 시각이 없다."""
    import uuid
    from datetime import UTC, datetime, timedelta

    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem

    pid = await new_project(client, admin_token, "people-expired")
    vid = await upload_video(client, admin_token, pid)
    rev_uid, _ = await add_member(client, admin_token, pid, "reviewer")
    _c, w = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    now = datetime.now(UTC)
    async with SessionLocal() as s:
        it = await s.get(WorkItem, uuid.UUID(w))
        it.status = "submitted"
        it.review_claimed_by = uuid.UUID(rev_uid)
        it.review_claimed_at = now - timedelta(hours=2)
        it.review_expires_at = now - timedelta(hours=1)   # 이전 버전이 남긴 만료 시각
        await s.commit()

    rows = (await client.get(
        f"/projects/{pid}/dashboard/labeler-performance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    rev = next(r for r in rows if r["user_id"] == rev_uid)
    assert rev["in_flight"] == 1


async def test_label_holding_is_the_same_count_across_screens(
    client: AsyncClient, admin_token: str
):
    """라벨링 보유 수가 내 작업·대기 수 응답·사람 표에서 같은지 확인한다.

    재작업이 돌아오면 한 사람이 두 건을 보유할 수 있다. 이전 버전이 남긴 지난 만료 시각은 보유 수에 영향을 주지 않는다.
    """
    import uuid
    from datetime import UTC, datetime, timedelta

    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem

    pid = await new_project(client, admin_token, "held-same")
    vid = await upload_video(client, admin_token, pid)
    uid, tok = await add_member(client, admin_token, pid, "annotator")
    _c1, live = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, stale = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    now = datetime.now(UTC)
    async with SessionLocal() as s:
        a = await s.get(WorkItem, uuid.UUID(str(live)))
        a.claimed_by, a.status = uuid.UUID(uid), "claimed"
        a.claimed_at = now
        b = await s.get(WorkItem, uuid.UUID(str(stale)))
        b.claimed_by, b.status, b.rejection_count = uuid.UUID(uid), "claimed", 1
        b.claimed_at, b.expires_at = now - timedelta(hours=3), now - timedelta(hours=1)
        await s.commit()

    lab = {"Authorization": f"Bearer {tok}"}
    summary = (await client.get(f"/projects/{pid}/work/available-count", headers=lab)).json()
    mine = (await client.get(f"/work/me?project_id={pid}", headers=lab)).json()
    rows = (await client.get(
        f"/projects/{pid}/dashboard/labeler-performance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    row = next(r for r in rows if r["user_id"] == uid)

    assert len(mine) == 2
    assert summary == {"count": 0, "held": 2}
    assert row["in_flight"] == 2


async def test_rejection_rate_denominator_is_volume_not_resolved(
    client: AsyncClient, admin_token: str
):
    """반려율의 분모가 판정이 끝난 건이 아니라 작업량(제출)이라서 통과율과의 합이 100%가 아닌지 확인한다.

    반려율을 작업량 대비로 정한 이유는 다음과 같다.
    분모가 판정이 끝난 건이면 반려율이 검수 속도에 따라 흔들린다.
    많이 낸 사람의 몇 건만 검수된 상태에서 하나가 반려되면 그 사람의 반려율이 크게 뛴다.
    그 분모로는 `approval_rate`와의 합도 항상 100%라서 두 수가 새 정보를 주지 않는다.
    이 테스트는 3건을 내고 2건만 판정한다(승인 1, 반려 1, 미검수 1).
    작업량 기준은 `1/3 ≈ 0.333`, 판정 기준은 `1/2 = 0.5`다.
    두 수가 다르므로 이 검사가 분모를 실제로 확인한다. 통과율은 판정 기준 그대로 50%다.
    """
    pid = await new_project(client, admin_token, "reject-denom")
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    _, rev_tok = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)

    submitted = []
    for i in range(3):
        await seed_clip_work(pid, vid, start=i * 10.0, end=i * 10.0 + 10.0)
        wid = (await client.post(
            f"/projects/{pid}/work/next", headers={"Authorization": f"Bearer {annot_tok}"}
        )).json()["work_item_id"]
        await client.post(
            f"/work/{wid}/submit", headers={"Authorization": f"Bearer {annot_tok}"},
            json={"payload": caption_payload("현황")},
        )
        submitted.append(wid)

    # 세 건 중 두 건만 판정한다.
    # 나머지 한 건은 작업량 분모에는 들어가고 판정 분모에는 들어가지 않는다.
    for wid, decision in zip(submitted[:2], ("approve", "reject"), strict=True):
        await claim_review(client, rev_tok, pid, wid)
        await client.post(
            f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev_tok}"},
            json={"decision": decision, "reason": "다시" if decision == "reject" else None},
        )

    row = next(
        r for r in (await client.get(
            f"/projects/{pid}/dashboard/labeler-performance",
            headers={"Authorization": f"Bearer {admin_token}"},
        )).json()
        if r["submitted"] == 3
    )
    assert (row["approved"], row["rejected"]) == (1, 1), row
    assert row["rejection_rate"] == pytest.approx(1 / 3), row   # 작업량 기준
    assert row["rejection_rate"] != pytest.approx(0.5), row     # 판정 기준 값(0.5)과 다르다
    assert row["approval_rate"] == 50.0, row                    # 통과율은 판정 기준 그대로
    # 두 값은 분모가 달라 합이 100%와 다르다.
    assert row["approval_rate"] / 100 + row["rejection_rate"] != pytest.approx(1.0), row



async def test_labeler_performance_rejection_base_follows_decisions_in_window(
    client: AsyncClient, admin_token: str
):
    """기간 안에 판정만 있고 제출이 없는 행이 비율과 같은 기준 수를 싣는지 확인한다.

    기간 전에 낸 작업이 기간 안에 반려되면 제출 0건, 반려 2건이 된다.
    반려율의 기준 수는 `max(제출, 승인+반려)`라서 2이고 반려율은 100%다.
    행이 기준 수를 싣지 않으면 화면은 제출 수로 괄호를 써서 `100% (2/0)`이 된다.
    """
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import Label, Review, WorkItem
    from sqlalchemy import select

    pid = await new_project(client, admin_token, "perf-base")
    uid, _ = await add_member(client, admin_token, pid, "annotator")
    rid, _ = await add_member(client, admin_token, pid, "reviewer")

    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    vids = [await upload_video(client, admin_token, pid, title=f"b{i}") for i in (1, 2)]
    async with SessionLocal() as s:
        for vid in vids:
            w = (await s.scalars(
                select(WorkItem).where(WorkItem.video_id == UUID(vid))
            )).one()
            w.claimed_by = UUID(uid)
            label = Label(work_item_id=w.work_item_id, submitted_by=UUID(uid),
                          label_data={}, submitted_at=old)
            s.add(label)
            await s.flush()
            s.add(Review(work_item_id=w.work_item_id, label_id=label.label_id,
                         reviewer_id=UUID(rid), verdict="reject",
                         reviewed_at=now - timedelta(hours=2)))
        await s.commit()

    async def row_for(user_id: str, **params) -> dict:
        """labeler-performance에서 한 사람의 행을 읽는다."""
        r = await client.get(
            f"/projects/{pid}/dashboard/labeler-performance",
            headers={"Authorization": f"Bearer {admin_token}"},
            params=params,
        )
        assert r.status_code == 200, r.text
        return next(x for x in r.json() if x["user_id"] == user_id)

    week = await row_for(uid, since=(now - timedelta(days=7)).isoformat())
    assert (week["submitted"], week["approved"], week["rejected"]) == (0, 0, 2), week
    assert week["rejection_base"] == 2, week
    assert week["rejection_rate"] == pytest.approx(1.0), week
    assert week["approval_base"] == 2 and week["approval_rate"] == 0.0, week

    # 전체 기간이면 제출이 기준 수다.
    whole = await row_for(uid)
    assert (whole["submitted"], whole["rejected"]) == (2, 2), whole
    assert whole["rejection_base"] == 2 and whole["approval_base"] == 2, whole

    # 판정 없는 멤버는 기준 수가 0이고 비율은 비어 있다.
    reviewer = await row_for(rid)
    assert (reviewer["approval_base"], reviewer["rejection_base"]) == (0, 0), reviewer
    assert reviewer["approval_rate"] is None and reviewer["rejection_rate"] is None, reviewer
