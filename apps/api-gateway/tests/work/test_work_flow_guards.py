"""라벨링·검수에서 화면이 막는 입력을 서버도 막는지 보는 테스트. 제출 입력, 대기열 순서, 이력 공개 범위, 검수 보유 만료가 대상이다."""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Clip, WorkItem
from httpx import AsyncClient
from tests._helpers import (
    add_member,
    caption_payload,
    claim_review,
    new_project,
    seed_clip_work,
    upload_video,
)


def _h(tok: str) -> dict:
    """Bearer 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


def _with_ranges(*ranges: tuple[float | None, float | None]) -> dict:
    """문장마다 이전 형식의 구간 키를 붙인 제출 payload를 만든다."""
    p = caption_payload("구간")
    text = p["caption"]["brief"]
    p["caption"]["brief_sentences"] = [
        {"id": f"s{i}", "text": text if i == 0 else "둘째 문장.", "start": a, "end": b}
        for i, (a, b) in enumerate(ranges)
    ]
    return p


# 라벨링


async def test_submit_ignores_leftover_sentence_ranges(client: AsyncClient, admin_token: str):
    """이전 초안에 남은 문장 구간 키를 검사하지 않고 제출을 받는지 확인한다.

    문장 구간 지정 기능이 없으므로 거꾸로 된 값이나 클립 밖 값이 남아 있어도 제출을 막지 않는다.
    """
    pid = await new_project(client, admin_token, "guard-range")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid, start=10.0, end=20.0)
    wid = (await client.post(f"/projects/{pid}/work/next", headers=_h(annot))).json()["work_item_id"]

    r = await client.post(f"/work/{wid}/submit", headers=_h(annot),
                          json={"payload": _with_ranges((15.0, 12.0), (5.0, 9999.0))})
    assert r.status_code == 200, r.text


async def test_claim_response_has_no_storage_path(client: AsyncClient, admin_token: str):
    """라벨링 가져오기 응답에 원본 저장 경로가 없는지 확인한다."""
    pid = await new_project(client, admin_token, "guard-uri")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    body = (await client.post(f"/projects/{pid}/work/next", headers=_h(annot))).json()
    assert "video_source_uri" not in body
    assert "s3://" not in str(body)


async def test_leftover_range_report_does_not_hold_the_queue(client: AsyncClient, admin_token: str):
    """이전에 남은 경계 신고 값이 있는 클립도 대기열에 나오는지 확인한다.

    경계 신고 기능이 없어 신고를 닫을 화면도 없다. 남은 값이 작업을 막으면 그 클립은 대기열에 다시 나오지 않는다.
    """
    pid = await new_project(client, admin_token, "guard-badrange")
    _, b = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid)
    clip_id, wid = await seed_clip_work(pid, vid)
    async with SessionLocal() as s:
        clip = await s.get(Clip, UUID(clip_id))
        clip.extra = {**(clip.extra or {}), "range_report": {"kind": "end_cut", "note": ""}}
        await s.commit()

    count = (await client.get(f"/projects/{pid}/work/available-count", headers=_h(b))).json()
    assert count["count"] == 1
    r = await client.post(f"/projects/{pid}/work/next", headers=_h(b))
    assert r.status_code == 200 and r.json()["work_item_id"] == wid


async def test_new_holder_does_not_receive_previous_labeler_history(
    client: AsyncClient, admin_token: str
):
    """대기열로 돌아온 재작업을 받은 라벨러가 이전 라벨러의 제출본과 반려 이력을 받지 않고 검수자는 전체를 보는지 확인한다."""
    pid = await new_project(client, admin_token, "guard-history")
    _, a = await add_member(client, admin_token, pid, "annotator")
    _, b = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (await client.post(f"/projects/{pid}/work/next", headers=_h(a))).json()["work_item_id"]
    for _ in range(3):
        await client.post(f"/work/{wid}/submit", headers=_h(a), json={"payload": caption_payload("이전")})
        await claim_review(client, rev, pid, wid)
        await client.post(f"/work/{wid}/review", headers=_h(rev),
                          json={"decision": "reject", "reason": "다시"})
    # A가 프로젝트를 나가면 재작업은 대기열로 돌아가고 B가 가져간다.
    r = await client.post(f"/projects/{pid}/me/leave", headers=_h(a), json={"reason": None})
    assert r.status_code == 200 and r.json()["released"] == 1, r.text
    r = await client.post(f"/projects/{pid}/work/next", headers=_h(b))
    assert r.json()["work_item_id"] == wid

    assert (await client.get(f"/work/{wid}/labels", headers=_h(b))).json() == []
    assert (await client.get(f"/work/{wid}/reviews", headers=_h(b))).json() == []
    assert (await client.get(f"/work/{wid}", headers=_h(b))).json()["payload"] == {}

    assert len((await client.get(f"/work/{wid}/labels", headers=_h(rev))).json()) == 3
    assert len((await client.get(f"/work/{wid}/reviews", headers=_h(rev))).json()) == 3

    # B가 제출하면 B는 자기 제출만 본다.
    await client.post(f"/work/{wid}/submit", headers=_h(b), json={"payload": caption_payload("새")})
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(wid))
        item.status = "claimed"
        await s.commit()
    labels = (await client.get(f"/work/{wid}/labels", headers=_h(b))).json()
    assert len(labels) == 1


# 검수


async def test_old_review_expiry_does_not_block_decide(client: AsyncClient, admin_token: str):
    """이전 버전이 남긴 지난 검수 만료 시각이 있어도 보유자가 초안을 저장하고 판정하는지 확인한다.

    검수 보유에는 끝나는 시각이 없다. 판정하거나 신고할 때까지 보유자의 검수다.
    """
    pid = await new_project(client, admin_token, "guard-revexp")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    _, rev2 = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (await client.post(f"/projects/{pid}/work/next", headers=_h(annot))).json()["work_item_id"]
    await client.post(f"/work/{wid}/submit", headers=_h(annot), json={"payload": caption_payload()})
    await claim_review(client, rev, pid, wid)
    async with SessionLocal() as s:
        (await s.get(WorkItem, UUID(wid))).review_expires_at = datetime.now(UTC) - timedelta(days=1)
        await s.commit()

    # 다른 검수자는 그 검수를 받지 않는다.
    r = await client.post(f"/projects/{pid}/review/next", headers=_h(rev2))
    assert r.status_code == 404 and r.json()["detail"] == "no_review_available", r.text
    r = await client.patch(f"/work/{wid}/review/draft", headers=_h(rev), json={"a": 1})
    assert r.status_code == 200, r.text
    r = await client.post(f"/work/{wid}/review", headers=_h(rev),
                          json={"decision": "reject", "reason": "늦어도 판정"})
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(wid))
        assert item.status == "claimed" and item.rejection_count == 1
