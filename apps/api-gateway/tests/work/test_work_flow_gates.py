"""작업 흐름 관문 테스트.

신고·제외는 작업을 멈추고, 삭제는 작업을 닫고, 보류는 사람이 푼다.
세 관문은 작업을 지금 사람에게 줄 수 있는지 정하는 기준 하나(`work.service._human_workable`)를 같이 쓴다.
그래서 한 파일에 둔다. 한 관문을 고치다 다른 관문의 전제를 깨면 여기서 함께 실패한다.
"""
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Clip, DomainEvent, Video, WorkItem
from api_gateway.services.analytics import stats as stats_service
from httpx import AsyncClient
from sqlalchemy import select, update
from tests._helpers import add_member, caption_payload, new_project, seed_clip_work, upload_video


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _claim(client: AsyncClient, token: str, pid: str) -> str | None:
    """라벨링 작업을 가져온다. 큐가 비어 404가 오면 `None`을 돌려준다."""
    r = await client.post(f"/projects/{pid}/work/next", headers=_h(token))
    return r.json()["work_item_id"] if r.status_code == 200 else None


async def _work_status(work_item_id: str) -> str:
    """작업의 현재 status를 읽는다."""
    async with SessionLocal() as s:
        return (await s.get(WorkItem, UUID(work_item_id))).status


async def _claimable(client: AsyncClient, token: str, pid: str) -> int:
    """지금 가져갈 수 있는 라벨링 작업 수를 조회한다."""
    r = await client.get(f"/projects/{pid}/work/available-count", headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()["count"]


# ── 신고하면 멈추고, 관리자의 처리에 따라 이어진다 ──────────────────────────


async def test_deident_report_stops_the_work(client: AsyncClient, admin_token: str):
    """신고하면 신고자가 작업을 놓고 작업이 큐에서도 빠지는지 확인한다."""
    pid = await new_project(client, admin_token, "d07a")
    _, tok = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid, title="d07a")
    clip_id, wid = await seed_clip_work(pid, vid)

    assert await _claim(client, tok, pid) == wid
    r = await client.post(
        f"/work/{wid}/report-deident-miss", headers=_h(tok),
        json={"timestamp_ms": 1200, "reason": "얼굴이 그대로 보입니다"},
    )
    assert r.status_code == 204, r.text

    # 점유가 풀린다. 내 작업 목록에서 빠지고 한도도 차지하지 않는다.
    assert await _work_status(wid) == "available"
    async with SessionLocal() as s:
        assert (await s.get(WorkItem, UUID(wid))).claimed_by is None
        assert (await s.get(Clip, UUID(clip_id))).rights_status == "legal_review"

    # 다시 가져갈 수는 없다. 관리자가 처리하기 전까지 사람 큐 밖에 있다.
    assert await _claimable(client, tok, pid) == 0
    assert await _claim(client, tok, pid) is None


async def test_resume_returns_the_reported_work(client: AsyncClient, admin_token: str):
    """재개하면 새 작업을 만들지 않고 같은 작업이 큐로 돌아오는지 확인한다."""
    pid = await new_project(client, admin_token, "d07b")
    _, tok = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid, title="d07b")
    clip_id, wid = await seed_clip_work(pid, vid)
    assert await _claim(client, tok, pid) == wid
    await client.post(
        f"/work/{wid}/report-deident-miss", headers=_h(tok),
        json={"timestamp_ms": 0, "reason": "번호판"},
    )

    r = await client.post(
        f"/clips/{clip_id}/deident-report/resolve", headers=_h(admin_token),
        json={"action": "resume", "note": "가림 확인"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rights_status"] == "auto_cleared" and body["resumed_work_items"] == 1
    assert body["closed_work_items"] == 0

    # 같은 work_item이 그대로 돌아온다. 부분 unique 인덱스가 새 작업 생성을 막는다.
    assert await _claimable(client, tok, pid) == 1
    assert await _claim(client, tok, pid) == wid


async def test_exclude_closes_the_work(client: AsyncClient, admin_token: str):
    """제외하면 작업이 바로 닫히는지 확인한다.

    닫지 않으면 라벨링·검수를 끝까지 거친 뒤 납품에서만 빠진다.
    """
    pid = await new_project(client, admin_token, "d07c")
    _, tok = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid, title="d07c")
    clip_id, wid = await seed_clip_work(pid, vid)
    assert await _claim(client, tok, pid) == wid
    await client.post(
        f"/work/{wid}/report-deident-miss", headers=_h(tok),
        json={"timestamp_ms": 0, "reason": "간판 상호"},
    )

    r = await client.post(
        f"/clips/{clip_id}/deident-report/resolve", headers=_h(admin_token),
        json={"action": "exclude", "note": "가림 불가"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rights_status"] == "rejected" and r.json()["closed_work_items"] == 1

    assert await _work_status(wid) == "cancelled"
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(wid))
        # 점유도 함께 푼다. 풀지 않으면 닫힌 작업이 내 작업 목록에 남아 한도를 차지한다.
        assert item.claimed_by is None and item.draft is None
        closed = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "work.closed",
            DomainEvent.target_id == UUID(wid),
        ))).all()
        assert len(closed) == 1 and closed[0].payload["reason"] == "deident_excluded"
    assert await _claimable(client, tok, pid) == 0


async def test_resolve_guards(client: AsyncClient, admin_token: str):
    """신고 처리는 관리자만 하고, 제외에는 사유가 필요하고, 신고 상태가 아니면 거절하는지 확인한다."""
    pid = await new_project(client, admin_token, "d07g")
    _, tok = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid, title="d07g")
    clip_id, wid = await seed_clip_work(pid, vid)
    url = f"/clips/{clip_id}/deident-report/resolve"
    r = await client.post(url, headers=_h(admin_token), json={"action": "resume"})
    assert r.status_code == 409 and r.json()["detail"] == "not_reported", r.text
    assert await _claim(client, tok, pid) == wid
    await client.post(
        f"/work/{wid}/report-deident-miss", headers=_h(tok),
        json={"timestamp_ms": 0, "reason": "얼굴"},
    )
    r = await client.post(url, headers=_h(tok), json={"action": "resume"})
    assert r.status_code == 403, r.text
    r = await client.post(url, headers=_h(admin_token), json={"action": "exclude", "note": "  "})
    assert r.status_code == 422 and r.json()["detail"] == "exclude_note_required", r.text
    async with SessionLocal() as s:
        assert (await s.get(Clip, UUID(clip_id))).rights_status == "legal_review"


async def test_reported_clip_leaves_the_review_queue(client: AsyncClient, admin_token: str):
    """제출된 작업을 검수자가 신고하면 검수 큐에서도 빠져 라벨링과 검수 양쪽에서 멈추는지 확인한다."""
    pid = await new_project(client, admin_token, "d07e")
    _, lab = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid, title="d07e")
    _, wid = await seed_clip_work(pid, vid)

    assert await _claim(client, lab, pid) == wid
    r = await client.post(
        f"/work/{wid}/submit", headers=_h(lab),
        json={"payload": caption_payload("관문")},
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/projects/{pid}/review/next", headers=_h(rev))
    assert r.status_code == 200 and r.json()["work_item_id"] == wid

    r = await client.post(
        f"/work/{wid}/report-deident-miss", headers=_h(rev),
        json={"timestamp_ms": 500, "reason": "주민번호가 보입니다"},
    )
    assert r.status_code == 204, r.text

    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(wid))
        assert item.status == "submitted", "제출 사실은 지우지 않는다"
        assert item.review_claimed_by is None and item.review_draft is None
    r = await client.get(f"/projects/{pid}/review/available-count", headers=_h(rev))
    assert r.status_code == 200 and r.json()["count"] == 0
    assert (await client.post(f"/projects/{pid}/review/next", headers=_h(rev))).status_code == 404


# ── 삭제하면 닫고, 집계에서 뺀다 ─────────────────────────────────────


async def test_delete_closes_unfinished_and_keeps_approved(
    client: AsyncClient, admin_token: str
):
    """영상 삭제가 미완료 작업만 닫고 납품에 남기기로 한 승인분은 그대로 두는지 확인한다."""
    pid = await new_project(client, admin_token, "d09a")
    vid = await upload_video(client, admin_token, pid, title="d09a")
    _, waiting = await seed_clip_work(pid, vid, start=0.0, end=5.0)
    _, done = await seed_clip_work(pid, vid, start=5.0, end=10.0)
    async with SessionLocal() as s:
        await s.execute(
            update(WorkItem).where(WorkItem.work_item_id == UUID(done))
            .values(status="approved")
        )
        await s.commit()

    r = await client.delete(f"/videos/{vid}", headers=_h(admin_token))
    assert r.status_code == 204, r.text

    assert await _work_status(waiting) == "cancelled"
    assert await _work_status(done) == "approved"
    async with SessionLocal() as s:
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "video.deleted",
            DomainEvent.target_id == UUID(vid),
        ))).all()
        assert ev[0].payload == {
            "closed_work_items": 1, "closed_dispatch_tickets": 1,
            "kept_approved_work_items": 1,
        }


async def test_deleted_video_drops_out_of_every_count(client: AsyncClient, admin_token: str):
    """삭제한 영상이 영상 수, 작업 수, 대기 수에서 함께 빠지는지 확인한다.

    한 화면의 수가 서로 다른 모집단에서 나오면 수끼리 맞지 않는다.
    """
    pid = await new_project(client, admin_token, "d09b")
    keep = await upload_video(client, admin_token, pid, title="keep")
    drop = await upload_video(client, admin_token, pid, title="drop")
    await seed_clip_work(pid, keep, start=0.0, end=5.0)
    await seed_clip_work(pid, drop, start=0.0, end=5.0)

    stats_service.reset_cache()
    before = (await client.get(f"/projects/{pid}/stats", headers=_h(admin_token))).json()
    assert before["total_videos"] == 2 and before["total_clips"] == 2
    assert before["work_by_status"]["available"] == 2

    assert (await client.delete(f"/videos/{drop}", headers=_h(admin_token))).status_code == 204
    stats_service.reset_cache()
    after = (await client.get(f"/projects/{pid}/stats", headers=_h(admin_token))).json()

    assert after["total_videos"] == 1, "설정 화면의 `영상 N`이 지운 영상을 계속 셌다"
    assert after["total_clips"] == 1
    assert after["work_by_status"].get("available") == 1
    assert after["work_by_status"].get("cancelled") is None, "닫힌 작업은 모집단 밖이다"
    # 시간 진척의 분모도 클립 하나(5초)만큼 줄어든다.
    assert after["total_hours"] == pytest.approx(before["total_hours"] / 2)


async def test_deleted_video_keeps_approved_hours_in_the_denominator(
    client: AsyncClient, admin_token: str
):
    """승인분을 남기면 진척률 분모에도 남기는지 확인한다. 분모에서 빼면 진척률이 100%를 넘는다."""
    pid = await new_project(client, admin_token, "d09c")
    vid = await upload_video(client, admin_token, pid, title="d09c")
    _, done = await seed_clip_work(pid, vid, start=0.0, end=36.0)
    async with SessionLocal() as s:
        await s.execute(
            update(WorkItem).where(WorkItem.work_item_id == UUID(done))
            .values(status="approved")
        )
        await s.commit()
    assert (await client.delete(f"/videos/{vid}", headers=_h(admin_token))).status_code == 204

    stats_service.reset_cache()
    st = (await client.get(f"/projects/{pid}/stats", headers=_h(admin_token))).json()
    assert st["total_clips"] == 1, "납품에 남는 것은 분모에도 남는다"
    assert st["work_hours_by_status"]["approved"] == pytest.approx(st["total_hours"])
    assert st["progress_pct"] == 100.0


async def test_deleted_video_work_never_returns_to_a_person(
    client: AsyncClient, admin_token: str
):
    """지운 영상의 작업이 가져오기로 오지 않고, 이미 가져간 사람의 점유도 풀리는지 확인한다."""
    pid = await new_project(client, admin_token, "d09d")
    _, tok = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid, title="d09d")
    _, wid = await seed_clip_work(pid, vid)
    assert await _claim(client, tok, pid) == wid

    assert (await client.delete(f"/videos/{vid}", headers=_h(admin_token))).status_code == 204
    assert await _work_status(wid) == "cancelled"
    assert await _claimable(client, tok, pid) == 0
    assert await _claim(client, tok, pid) is None
    r = await client.get("/work/me", headers=_h(tok))
    assert all(w["work_item_id"] != wid for w in r.json())


# ── 보류를 사람이 푼다 ───────────────────────────────────────────────


async def _held_work(client: AsyncClient, admin_token: str, pid: str) -> str:
    """승인되고 품질검수 보류인 작업 하나를 만든다. 검사를 돌리지 않고 상태만 만든다.

    러너를 실제로 돌리면 모델 연결과 사전 설정이 필요해서 테스트가 그 설정까지 검증하게 된다.
    이 테스트의 대상은 보류를 푸는 경로라서 출발 상태만 만든다.
    """
    vid = await upload_video(client, admin_token, pid, title="qc")
    _, wid = await seed_clip_work(pid, vid)
    async with SessionLocal() as s:
        await s.execute(
            update(WorkItem).where(WorkItem.work_item_id == UUID(wid))
            .values(status="approved", qc_status="held")
        )
        await s.commit()
    return wid


async def test_admin_releases_the_hold_with_a_reason(client: AsyncClient, admin_token: str):
    """관리자가 보류를 풀면 사유와 당시 지적 목록이 감사 기록에 남는지 확인한다."""
    pid = await new_project(client, admin_token, "d12a")
    wid = await _held_work(client, admin_token, pid)

    r = await client.post(
        f"/projects/{pid}/qc/{wid}/release", headers=_h(admin_token),
        json={"reason": "정당한 인용이라 비속어 사전 오탐입니다"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["qc_status"] == "passed"

    async with SessionLocal() as s:
        assert (await s.get(WorkItem, UUID(wid))).qc_status == "passed"
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "caption_qc.hold_released",
            DomainEvent.target_id == UUID(wid),
        ))).all()
        assert len(ev) == 1
        assert ev[0].payload["reason"].startswith("정당한 인용")
        assert "findings_at_release" in ev[0].payload


async def test_release_requires_a_reason_and_an_admin(client: AsyncClient, admin_token: str):
    """사유가 없거나 관리자가 아니면 보류를 풀 수 없는지 확인한다. 보류 해제는 납품 게이트를 넘기는 일이다."""
    pid = await new_project(client, admin_token, "d12b")
    wid = await _held_work(client, admin_token, pid)
    _, ann = await add_member(client, admin_token, pid, "annotator")

    r = await client.post(f"/projects/{pid}/qc/{wid}/release", headers=_h(admin_token), json={})
    assert r.status_code == 422, "사유는 필수다"
    r = await client.post(
        f"/projects/{pid}/qc/{wid}/release", headers=_h(admin_token), json={"reason": "  "},
    )
    assert r.status_code == 422, "공백만 적은 것은 안 적은 것이다"

    r = await client.post(
        f"/projects/{pid}/qc/{wid}/release", headers=_h(ann), json={"reason": "풀어 주세요"},
    )
    assert r.status_code == 403 and r.json()["detail"] == "not_project_admin"


async def test_release_refuses_when_it_is_not_held(client: AsyncClient, admin_token: str):
    """이미 풀린 보류를 다시 풀려고 하면 409를 주는지 확인한다.

    이 요청은 화면 목록이 오래된 경우에 온다. 조용히 넘기면 사용자가 목록이 오래된 것을 알 수 없다.
    """
    pid = await new_project(client, admin_token, "d12c")
    wid = await _held_work(client, admin_token, pid)
    await client.post(
        f"/projects/{pid}/qc/{wid}/release", headers=_h(admin_token), json={"reason": "오탐"},
    )
    r = await client.post(
        f"/projects/{pid}/qc/{wid}/release", headers=_h(admin_token), json={"reason": "또"},
    )
    assert r.status_code == 409 and r.json()["detail"] == "qc_not_held"


async def test_recheck_runs_only_the_check(client: AsyncClient, admin_token: str):
    """다시 검사가 승인 없이 검사만 돌리고, 검사가 꺼져 있으면 보류를 그대로 두는지 확인한다.

    기본 프로젝트는 캡션 품질검수가 꺼져 있다(`settings.enabled=False`).
    그때 러너는 아무것도 하지 않고, 이 버튼은 보류를 풀지 않는다.
    보류는 사람이 판정해야 풀린다.
    """
    pid = await new_project(client, admin_token, "d12d")
    wid = await _held_work(client, admin_token, pid)

    r = await client.post(f"/projects/{pid}/qc/{wid}/recheck", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["qc_status"] == "held" and body["verdict"] is None

    async with SessionLocal() as s:
        assert (await s.get(WorkItem, UUID(wid))).qc_status == "held"
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "caption_qc.rechecked",
            DomainEvent.target_id == UUID(wid),
        ))).all()
        assert len(ev) == 1 and ev[0].payload["ran"] is False


async def test_qc_actions_refuse_another_projects_work(client: AsyncClient, admin_token: str):
    """주소의 프로젝트와 작업의 프로젝트가 다르면 404를 주는지 확인한다. 권한은 주소의 프로젝트로 검사한다."""
    mine = await new_project(client, admin_token, "d12e")
    other = await new_project(client, admin_token, "d12f")
    wid = await _held_work(client, admin_token, other)

    r = await client.post(
        f"/projects/{mine}/qc/{wid}/release", headers=_h(admin_token), json={"reason": "x"},
    )
    assert r.status_code == 404 and r.json()["detail"] == "work_item_not_found"


# ── 회귀 방지: 삭제한 영상이 다른 화면의 수에도 남지 않는다 ─────────────────


async def test_video_dto_reports_what_delete_would_close(client: AsyncClient, admin_token: str):
    """삭제 확인 창의 근거 수가 미완료 작업만 세어 `rejected`는 넣고 `approved`는 빼는지 확인한다."""
    pid = await new_project(client, admin_token, "d09f")
    vid = await upload_video(client, admin_token, pid, title="d09f")
    _, a = await seed_clip_work(pid, vid, start=0.0, end=5.0)
    _, b = await seed_clip_work(pid, vid, start=5.0, end=10.0)
    _, c = await seed_clip_work(pid, vid, start=10.0, end=15.0)
    async with SessionLocal() as s:
        await s.execute(
            update(WorkItem).where(WorkItem.work_item_id == UUID(b)).values(status="rejected")
        )
        await s.execute(
            update(WorkItem).where(WorkItem.work_item_id == UUID(c)).values(status="approved")
        )
        await s.commit()

    r = await client.get(f"/videos/{vid}", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["open_work_items"] == 2, "대기 1 + 반려 1. 승인분은 안 닫힌다"

    assert (await client.delete(f"/videos/{vid}", headers=_h(admin_token))).status_code == 204
    async with SessionLocal() as s:
        assert (await s.get(Video, UUID(vid))).deleted_at is not None
        assert await _work_status(a) == "cancelled"
        assert await _work_status(b) == "cancelled"
        assert await _work_status(c) == "approved"


# ── 다른 사람의 claim은 super_admin도 건드리지 못한다 ────────────────────────────
# 라벨링의 `_load_my_claim`과 검수의 `_require_review_holder`는 super_admin에게도 예외를 두지 않는 같은 규칙을 쓴다.
# 화면은 자기 점유만 열므로 면제가 있으면 API를 직접 호출할 때만 쓰인다.


async def _labeler_holding(client: AsyncClient, admin_token: str, slug: str):
    """annotator 한 명이 작업 하나를 가져간 상태를 만든다. (wid, 라벨러 헤더)를 돌려준다."""
    pid = await new_project(client, admin_token, slug)
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    _uid, tok = await add_member(client, admin_token, pid, "annotator")
    h = {"Authorization": f"Bearer {tok}"}
    r = await client.post(f"/projects/{pid}/work/next", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["work_item_id"], h


async def test_super_admin_cannot_touch_someone_elses_claim(
    client: AsyncClient, admin_token: str,
):
    """다른 사람이 점유한 작업의 초안 저장, 제출, 비식별화 신고가 모두 403인지 확인한다. 셋 다 다른 사람의 작업을 덮거나 가로채거나 풀어 버린다."""
    wid, h = await _labeler_holding(client, admin_token, "co-guard")
    ha = {"Authorization": f"Bearer {admin_token}"}

    for method, path, body in (
        ("patch", f"/work/{wid}/draft", {"payload": {"caption": "관리자가 덮어쓴다"}}),
        ("post", f"/work/{wid}/submit", {"payload": caption_payload()}),
        ("post", f"/work/{wid}/report-deident-miss", {"timestamp_ms": 0, "reason": "관리자 신고"}),
    ):
        r = await getattr(client, method)(path, headers=ha, **({"json": body} if body else {}))
        assert r.status_code == 403, f"{path} → {r.status_code} {r.text}"
        assert r.json()["detail"] == "not_your_claim"


async def test_the_holders_own_draft_survives(client: AsyncClient, admin_token: str):
    """403이 난 뒤에도 보유자의 초안이 남아 있는지 확인한다.

    신고는 보유를 풀며 초안을 지우므로, 403 전에 초안이 지워지면 거절 응답만으로는 그 손실을 잡지 못한다.
    """
    wid, h = await _labeler_holding(client, admin_token, "co-draft")
    ha = {"Authorization": f"Bearer {admin_token}"}
    r = await client.patch(f"/work/{wid}/draft", headers=h, json={"payload": {"caption": "내 초안"}})
    assert r.status_code in (200, 204), r.text

    await client.post(f"/work/{wid}/report-deident-miss", headers=ha,
                      json={"timestamp_ms": 0, "reason": "관리자 신고"})
    await client.patch(f"/work/{wid}/draft", headers=ha, json={"payload": {"caption": "덮어쓰기"}})

    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(wid))
    assert item.status == "claimed", "관리자가 남의 작업을 큐로 되돌렸다"
    assert item.draft == {"caption": "내 초안"}, f"초안이 바뀌었다: {item.draft}"


async def test_the_holder_still_can(client: AsyncClient, admin_token: str):
    """보유자 본인은 그대로 할 수 있는지 확인하는 대조군이다. 이 테스트가 없으면 모두 막는 구현도 통과한다."""
    wid, h = await _labeler_holding(client, admin_token, "co-owner")
    r = await client.patch(f"/work/{wid}/draft", headers=h, json={"payload": {"caption": "내 것"}})
    assert r.status_code in (200, 204), r.text
    r = await client.post(f"/work/{wid}/submit", headers=h, json={"payload": caption_payload()})
    assert r.status_code == 200, r.text
