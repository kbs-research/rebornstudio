"""큐 서버 규칙을 확인하는 회귀 테스트.

비식별화 신고는 검수 보유자도 할 수 있고, 신고 목록에 사유와 시각이 함께 온다.
검수 대기 수는 서버가 가져오기와 같은 조건으로 세고, 다른 사람이 가져간 건은 대기로 세지 않는다.
라벨링 보유 수는 서버가 세고, 보유한 작업은 시간이 지나도 다른 사람에게 가지 않는다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Clip, WorkItem
from httpx import AsyncClient
from sqlalchemy import update
from tests._helpers import (
    add_member,
    caption_payload,
    new_project,
    seed_clip_work,
    upload_video,
)


async def test_review_available_count_excludes_held_by_others(client: AsyncClient, admin_token: str):
    """검수 대기 수를 가져오기와 같은 조건으로 세어 다른 사람이 가져간 검수를 대기에서 빼는지 확인한다."""
    pid = await new_project(client, admin_token, "rq06")
    _, tok_l = await add_member(client, admin_token, pid, "annotator")
    _, tok_r1 = await add_member(client, admin_token, pid, "reviewer")
    _, tok_r2 = await add_member(client, admin_token, pid, "reviewer")
    hl, h1, h2 = ({"Authorization": f"Bearer {t}"} for t in (tok_l, tok_r1, tok_r2))
    vid = await upload_video(client, admin_token, pid)
    for i in range(2):
        await seed_clip_work(pid, vid, start=i * 5.0, end=i * 5.0 + 5.0)
    # 라벨러가 두 건 제출
    for _ in range(2):
        wid = (await client.post(f"/projects/{pid}/work/next", headers=hl)).json()["work_item_id"]
        r = await client.post(f"/work/{wid}/submit", headers=hl, json={"payload": caption_payload("대기 수")})
        assert r.status_code == 200, r.text

    c = (await client.get(f"/projects/{pid}/review/available-count", headers=h2)).json()
    assert c == {"count": 2, "held": 0}, c
    # r1이 한 건 가져가면 r2의 대기는 1이다
    assert (await client.post(f"/projects/{pid}/review/next", headers=h1)).status_code == 200
    c = (await client.get(f"/projects/{pid}/review/available-count", headers=h2)).json()
    assert c["count"] == 1, c
    c1 = (await client.get(f"/projects/{pid}/review/available-count", headers=h1)).json()
    assert c1["held"] == 1, c1
    # 라벨러(멤버)도 수는 본다
    assert (await client.get(f"/projects/{pid}/review/available-count", headers=hl)).status_code == 200


async def test_labeling_count_carries_my_held_and_project_scoped_mine(client: AsyncClient, admin_token: str):
    """라벨링 대기 수 응답이 본인의 보유 수를 함께 주고 `/work/me?project_id=`가 그 프로젝트로 거르는지 확인한다.

    응답에 한도는 없다. 한 사람이 한 건씩 맡는다.
    """
    p1 = await new_project(client, admin_token, "rq07a")
    p2 = await new_project(client, admin_token, "rq07b")
    _uid, tok = await add_member(client, admin_token, p1, "annotator")
    hdr = {"Authorization": f"Bearer {tok}"}
    vid = await upload_video(client, admin_token, p1)
    await seed_clip_work(p1, vid)
    await seed_clip_work(p1, vid, start=5.0, end=10.0)
    c = (await client.get(f"/projects/{p1}/work/available-count", headers=hdr)).json()
    assert c == {"count": 2, "held": 0}, c
    assert (await client.post(f"/projects/{p1}/work/next", headers=hdr)).status_code == 200
    c = (await client.get(f"/projects/{p1}/work/available-count", headers=hdr)).json()
    assert c == {"count": 1, "held": 1}, c
    # 한 번 더 눌러도 새 작업이 배정되지 않는다.
    assert (await client.post(f"/projects/{p1}/work/next", headers=hdr)).json()["resumed"] is True
    c = (await client.get(f"/projects/{p1}/work/available-count", headers=hdr)).json()
    assert c == {"count": 1, "held": 1}, c
    mine_all = (await client.get("/work/me", headers=hdr)).json()
    mine_p2 = (await client.get("/work/me", headers=hdr, params={"project_id": p2})).json()
    assert len(mine_all) == 1 and mine_p2 == [], (mine_all, mine_p2)


async def test_reviewer_can_report_deident_miss_on_held_review(client: AsyncClient, admin_token: str):
    """검수를 가져간 검수자도 비식별화 미흡을 신고할 수 있는지 확인한다.

    라벨링 점유만 받으면 검수자의 신고는 늘 409가 된다.
    """
    pid = await new_project(client, admin_token, "lr01")
    _, tok_l = await add_member(client, admin_token, pid, "annotator")
    _, tok_r = await add_member(client, admin_token, pid, "reviewer")
    hl, hr = {"Authorization": f"Bearer {tok_l}"}, {"Authorization": f"Bearer {tok_r}"}
    vid = await upload_video(client, admin_token, pid)
    clip_id, _ = await seed_clip_work(pid, vid)
    wid = (await client.post(f"/projects/{pid}/work/next", headers=hl)).json()["work_item_id"]
    assert (await client.post(f"/work/{wid}/submit", headers=hl, json={"payload": caption_payload("대기 수")})).status_code == 200
    # 검수자가 아직 가져가지 않았으면 거절한다. 다른 사람의 제출을 아무나 신고하지 못한다.
    r = await client.post(f"/work/{wid}/report-deident-miss", headers=hr, json={"timestamp_ms": 1200, "reason": "얼굴 노출"})
    assert r.status_code in (403, 409), r.text
    assert (await client.post(f"/projects/{pid}/review/next", headers=hr)).status_code == 200
    r = await client.post(f"/work/{wid}/report-deident-miss", headers=hr, json={"timestamp_ms": 1200, "reason": "얼굴 노출"})
    assert r.status_code == 204, r.text
    async with SessionLocal() as s:
        assert (await s.get(Clip, UUID(clip_id))).rights_status == "legal_review"


async def test_deident_report_row_carries_the_report(client: AsyncClient, admin_token: str):
    """비식별화 미흡 신고로 멈춘 클립의 신고 목록 행에 신고 사유·시각·신고자가 함께 오는지 확인한다."""
    pid = await new_project(client, admin_token, "rq13")
    hdr = {"Authorization": f"Bearer {admin_token}"}
    _, tok_l = await add_member(client, admin_token, pid, "annotator")
    hl = {"Authorization": f"Bearer {tok_l}"}
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (await client.post(f"/projects/{pid}/work/next", headers=hl)).json()["work_item_id"]
    r = await client.post(f"/work/{wid}/report-deident-miss", headers=hl,
                          json={"timestamp_ms": 3200, "reason": "취재원 얼굴 노출"})
    assert r.status_code == 204, r.text
    page = (await client.get(f"/projects/{pid}/deident-reports", headers=hdr)).json()
    assert page["total"] == 1 and len(page["items"]) == 1, page
    rep = page["items"][0]["report"]
    assert rep and rep["reason"] == "취재원 얼굴 노출" and rep["timestamp_ms"] == 3200, rep
    assert rep["reporter_name"] == "annotator", rep


async def test_concurrent_labeling_claims_never_hand_out_the_same_item(
    client: AsyncClient, admin_token: str,
):
    """세 라벨러가 동시에 가져와도 한 작업이 한 사람에게만 가는지 확인한다.

    작업이 2건이면 두 명은 받고 한 명은 404를 받는다.
    """
    import asyncio

    pid = await new_project(client, admin_token, "rq29")
    vid = await upload_video(client, admin_token, pid)
    for i in range(2):
        await seed_clip_work(pid, vid, start=i * 5.0, end=i * 5.0 + 5.0)
    tokens = [(await add_member(client, admin_token, pid, "annotator"))[1] for _ in range(3)]
    results = await asyncio.gather(*[
        client.post(f"/projects/{pid}/work/next", headers={"Authorization": f"Bearer {t}"})
        for t in tokens
    ], return_exceptions=True)
    codes = sorted(r.status_code for r in results if hasattr(r, "status_code"))
    assert codes == [200, 200, 404], codes
    got = [r.json()["work_item_id"] for r in results if hasattr(r, "status_code") and r.status_code == 200]
    assert len(set(got)) == 2, got


async def _old_expiry(work_item_id: str, *, rejection_count: int | None = None) -> None:
    """이전 버전이 남긴 지난 만료 시각을 점유에 적는다. 지금은 보유에 끝나는 시각이 없다."""
    values: dict = {"expires_at": datetime.now(UTC) - timedelta(days=2)}
    if rejection_count is not None:
        values["rejection_count"] = rejection_count
    async with SessionLocal() as s:
        await s.execute(update(WorkItem).where(WorkItem.work_item_id == UUID(work_item_id)).values(**values))
        await s.commit()


async def test_new_assignment_starts_without_a_leftover_draft(
    client: AsyncClient, admin_token: str,
):
    """대기열의 작업에 이전 사람의 초안이 남아 있어도 새로 배정할 때 비우는지 확인한다.

    보유를 푸는 경로(신고·멤버 제거)가 초안을 비우는 것과 같은 규칙이다.
    """
    pid = await new_project(client, admin_token, "lr-draft")
    vid = await upload_video(client, admin_token, pid)
    _c, wid = await seed_clip_work(pid, vid)
    _, tok_b = await add_member(client, admin_token, pid, "annotator")
    hb = {"Authorization": f"Bearer {tok_b}"}
    async with SessionLocal() as s:
        await s.execute(update(WorkItem).where(WorkItem.work_item_id == UUID(str(wid)))
                        .values(draft={"caption": "이전 사람의 초안"}))
        await s.commit()

    got = (await client.post(f"/projects/{pid}/work/next", headers=hb)).json()
    assert got["work_item_id"] == str(wid)
    async with SessionLocal() as s:
        assert (await s.get(WorkItem, UUID(str(wid)))).draft is None, "이전 라벨러의 초안이 따라왔다"


async def test_held_work_never_passes_to_someone_else(client: AsyncClient, admin_token: str):
    """보유한 작업은 시간이 지나도 다른 사람에게 가지 않고, 보유자는 초안 그대로 다시 여는지 확인한다.

    자동 회수가 없으므로 이전 버전이 남긴 지난 만료 시각도 보유를 끝내지 않는다. 새 작업도 재작업도 같다.
    """
    pid = await new_project(client, admin_token, "lr-rework")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    await seed_clip_work(pid, vid, start=5.0, end=10.0)
    _, tok_a = await add_member(client, admin_token, pid, "annotator")
    _, tok_b = await add_member(client, admin_token, pid, "annotator")
    ha, hb = {"Authorization": f"Bearer {tok_a}"}, {"Authorization": f"Bearer {tok_b}"}

    wid = (await client.post(f"/projects/{pid}/work/next", headers=ha)).json()["work_item_id"]
    await client.patch(f"/work/{wid}/draft", headers=ha, json={"payload": {"caption": "고치던 문장"}})
    other = (await client.post(f"/projects/{pid}/work/next", headers=hb)).json()["work_item_id"]
    assert other != wid
    await _old_expiry(wid, rejection_count=1)

    # B는 이미 한 건을 보유하므로 다음 작업이 B의 작업을 다시 연다. A의 작업은 오지 않는다.
    again_b = (await client.post(f"/projects/{pid}/work/next", headers=hb)).json()
    assert again_b["work_item_id"] == other
    assert (await client.get(f"/projects/{pid}/work/available-count", headers=hb)).json() == {
        "count": 0, "held": 1}

    got = await client.post(f"/projects/{pid}/work/next", headers=ha)
    assert got.status_code == 200, got.text
    assert (got.json()["work_item_id"], got.json()["resumed"]) == (wid, True)
    async with SessionLocal() as s:
        assert (await s.get(WorkItem, UUID(wid))).draft == {"caption": "고치던 문장"}
    r = await client.post(f"/work/{wid}/submit", headers=ha, json={"payload": caption_payload("제출")})
    assert r.status_code == 200, r.text
