"""기간 안의 프로젝트 합계(`labeler-performance/summary`) 테스트.

통계 화면 위쪽 요약이 이 합계를 쓰고 아래 사람별 표가 `labeler-performance`를 쓴다.
두 응답의 합계가 다르면 같은 화면의 위아래가 다른 수를 보인다. 그래서 합계가 같은 기간 행의 합과 같은지를 확인한다.
"""
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from tests._helpers import add_member, make_user, new_project, seed_clip_work, upload_video

SUMMED = ("submitted", "approved", "rejected", "edit_measured", "edit_distance", "draft_chars")


def _h(token: str) -> dict[str, str]:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _get(client: AsyncClient, token: str, pid: str, path: str, since: datetime | None):
    """대시보드 응답을 읽는다. `since`가 있으면 쿼리로 넘긴다."""
    params = {"since": since.isoformat()} if since else {}
    r = await client.get(f"/projects/{pid}/dashboard/{path}", headers=_h(token), params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def _work_items(client: AsyncClient, admin_token: str, pid: str, n: int) -> list[uuid.UUID]:
    """작업 n건을 만든다. 영상 단위 작업은 영상마다 하나라서 건마다 영상을 따로 둔다."""
    out = []
    for i in range(n):
        vid = await upload_video(client, admin_token, pid, title=f"sum-{i}")
        _c, wid = await seed_clip_work(pid, vid)
        out.append(uuid.UUID(wid))
    return out


async def test_summary_equals_row_sums(client: AsyncClient, admin_token: str):
    """합계의 수가 같은 기간 사람별 행의 합과 같고 비율은 합계에 같은 규칙을 적용한 값인지 확인한다.

    사람은 셋이다. 라벨러는 기간 안에 세 건을 내고 승인 1·반려 1을 받는다.
    멤버가 아닌 제출자는 기간 안에 한 건을 낸다. 나간 멤버(멤버가 아닌 사용자)는 기간 전에 낸 작업이
    기간 안에 반려된다. 나간 멤버의 행이 없으면 합계의 판정이 표의 합보다 하나 많아진다.
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import Label, Review

    pid = await new_project(client, admin_token, "sum-rows")
    annot, _ = await add_member(client, admin_token, pid, "annotator")
    rev, _ = await add_member(client, admin_token, pid, "reviewer")
    outsider, _ = await make_user(client, admin_token, "sum-out")
    gone, _ = await make_user(client, admin_token, "sum-gone")
    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    w = await _work_items(client, admin_token, pid, 5)
    async with SessionLocal() as s:
        labels = [
            Label(work_item_id=w[0], submitted_by=uuid.UUID(annot), label_data={},
                  submitted_at=now, ai_draft="초안", edit_distance=10, draft_chars=100),
            Label(work_item_id=w[1], submitted_by=uuid.UUID(annot), label_data={},
                  submitted_at=now, ai_draft="초안", edit_distance=30, draft_chars=50),
            Label(work_item_id=w[2], submitted_by=uuid.UUID(annot), label_data={}, submitted_at=now),
            Label(work_item_id=w[3], submitted_by=uuid.UUID(outsider), label_data={},
                  submitted_at=now, ai_draft="초안", edit_distance=5, draft_chars=50),
            Label(work_item_id=w[4], submitted_by=uuid.UUID(gone), label_data={}, submitted_at=old),
        ]
        s.add_all(labels)
        await s.flush()
        for label, verdict in ((labels[0], "approve"), (labels[1], "reject"), (labels[4], "reject")):
            s.add(Review(work_item_id=label.work_item_id, reviewer_id=uuid.UUID(rev),
                         verdict=verdict, label_id=label.label_id, reviewed_at=now))
        await s.commit()

    since = now - timedelta(days=7)
    for window in (None, since):
        rows = await _get(client, admin_token, pid, "labeler-performance", window)
        summary = await _get(client, admin_token, pid, "labeler-performance/summary", window)
        for key in SUMMED:
            assert summary[key] == sum(r[key] for r in rows), (window, key, summary, rows)
        assert summary["submitters"] == sum(1 for r in rows if r["submitted"] > 0)
        assert summary["approval_base"] == summary["approved"] + summary["rejected"]

    # 7일 기간의 값이다. 제출 4건(라벨러 3, 멤버 아닌 제출자 1), 판정 3건(승인 1, 반려 2).
    assert gone in {r["user_id"] for r in rows}, rows
    gone_row = next(r for r in rows if r["user_id"] == gone)
    assert (gone_row["role"], gone_row["submitted"], gone_row["rejected"]) == (None, 0, 1)
    assert (summary["submitted"], summary["submitters"]) == (4, 2)
    assert (summary["approved"], summary["rejected"], summary["approval_base"]) == (1, 2, 3)
    assert summary["approval_rate"] == 33.3
    # 반려율의 분모는 행별 분모의 합(3 + 1 + 1 = 5)이 아니라 max(제출 합, 판정 합)이다.
    assert summary["rejection_base"] == 4
    assert summary["rejection_rate"] == 0.5
    # 수정률은 글자 수 합의 비율이다. (10 + 30 + 5) / (100 + 50 + 50) = 22.5%.
    assert (summary["edit_distance"], summary["draft_chars"], summary["edit_measured"]) == (45, 200, 3)
    assert summary["edit_rate"] == 22.5


async def test_summary_empty_project(client: AsyncClient, admin_token: str):
    """제출도 판정도 없으면 수는 0이고 비율은 모두 `None`인지 확인한다. 0으로 나눈 값을 내지 않는다."""
    pid = await new_project(client, admin_token, "sum-empty")
    await add_member(client, admin_token, pid, "annotator")
    summary = await _get(client, admin_token, pid, "labeler-performance/summary", None)
    assert summary == {
        "submitted": 0, "submitters": 0, "approved": 0, "rejected": 0,
        "approval_rate": None, "rejection_rate": None, "approval_base": 0, "rejection_base": 0,
        "edit_rate": None, "edit_measured": 0, "edit_distance": 0, "draft_chars": 0,
    }


async def test_summary_decisions_without_submissions(client: AsyncClient, admin_token: str):
    """기간 안에 제출은 없고 판정만 있으면 승인율·반려율은 판정 수로 나누고 수정률은 `None`인지 확인한다.

    기간 전에 낸 작업이 기간 안에 판정되는 경우다. 반려율을 제출 수(0)로 나누면 값을 낼 수 없으므로
    판정 수가 분모가 된다.
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import Label, Review

    pid = await new_project(client, admin_token, "sum-late")
    annot, _ = await add_member(client, admin_token, pid, "annotator")
    rev, _ = await add_member(client, admin_token, pid, "reviewer")
    now = datetime.now(UTC)
    w = await _work_items(client, admin_token, pid, 2)
    async with SessionLocal() as s:
        for wid, verdict in zip(w, ("approve", "reject"), strict=True):
            label = Label(work_item_id=wid, submitted_by=uuid.UUID(annot), label_data={},
                          submitted_at=now - timedelta(days=30), ai_draft="초안", edit_distance=4, draft_chars=40)
            s.add(label)
            await s.flush()
            s.add(Review(work_item_id=wid, reviewer_id=uuid.UUID(rev), verdict=verdict,
                         label_id=label.label_id, reviewed_at=now))
        await s.commit()

    summary = await _get(
        client, admin_token, pid, "labeler-performance/summary", now - timedelta(days=7),
    )
    assert (summary["submitted"], summary["submitters"]) == (0, 0)
    assert (summary["approval_base"], summary["approval_rate"]) == (2, 50.0)
    assert (summary["rejection_base"], summary["rejection_rate"]) == (2, 0.5)
    assert (summary["edit_rate"], summary["draft_chars"]) == (None, 0)


async def test_summary_is_admin_only(client: AsyncClient, admin_token: str):
    """라벨러는 프로젝트 합계를 읽을 수 없는지 확인한다. 사람별 실적과 같은 권한이다."""
    pid = await new_project(client, admin_token, "sum-authz")
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.get(f"/projects/{pid}/dashboard/labeler-performance/summary", headers=_h(annot_tok))
    assert r.status_code == 403, r.text


def test_summarize_uses_totals_not_row_rates():
    """합계의 비율이 행별 비율의 평균이 아니라 합계에 같은 규칙을 적용한 값인지 확인한다.

    제출 1건을 반려받은 사람(100%)과 99건을 내고 반려가 없는 사람(0%)의 평균은 50%다.
    합계 기준은 1/100 = 1%다.
    """
    from api_gateway.services.analytics.dashboard.queries import _summarize

    zero = {"edit_measured": 0, "edit_distance": 0, "draft_chars": 0}
    s = _summarize([
        {"submitted": 1, "approved": 0, "rejected": 1, **zero},
        {"submitted": 99, "approved": 0, "rejected": 0, **zero},
    ])
    assert (s.submitted, s.submitters, s.rejection_base, s.rejection_rate) == (100, 2, 100, 0.01)
    assert s.approval_rate == 0.0 and s.approval_base == 1
    assert _summarize([]).model_dump() == {
        "submitted": 0, "submitters": 0, "approved": 0, "rejected": 0,
        "approval_rate": None, "rejection_rate": None, "approval_base": 0, "rejection_base": 0,
        "edit_rate": None, "edit_measured": 0, "edit_distance": 0, "draft_chars": 0,
    }
