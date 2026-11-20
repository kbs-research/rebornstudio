"""수정 후 승인도 라벨러의 승인으로 세는지 보는 테스트.

수정 후 승인은 새 리비전을 만들고, `Review.label_id`는 그 리비전을 가리킨다.
그 리비전을 쓴 사람은 검수자다.
그대로 세면 라벨러는 자기 작업이 수정 후 승인될 때마다 승인 하나를 잃고, 검수자가 많이 고친 라벨러일수록 승인율이 낮아진다.
검수 승인율은 사람이 판정할 때 보는 객관 지표 넷 중 하나다.
화면은 라벨러에게 관리자가 보는 수와 같다고 안내한다.
관리자가 보는 수와 라벨러가 보는 수가 함께 틀리면 이의 제기를 받아도 근거가 나오지 않는다.
"""
from httpx import AsyncClient
from tests._helpers import (
    add_member,
    caption_payload,
    claim_review,
    latest_label_id,
    new_project,
    seed_clip_work,
    upload_video,
)


async def _submit_one(client: AsyncClient, admin_token: str, pid: str, vid: str, tok: str) -> str:
    """라벨러가 한 건을 제출하고 work_item_id를 돌려준다."""
    await seed_clip_work(pid, vid, start=0.0, end=10.0)
    r = await client.post(f"/projects/{pid}/work/next", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    wid = r.json()["work_item_id"]
    r = await client.post(
        f"/work/{wid}/submit", headers={"Authorization": f"Bearer {tok}"},
        json={"payload": caption_payload()},
    )
    assert r.status_code == 200, r.text
    return wid


async def test_modify_and_approve_counts_for_the_labeler(client: AsyncClient, admin_token: str):
    """검수자가 고쳐서 승인해도 그 승인을 라벨러의 것으로 세는지 확인한다."""
    pid = await new_project(client, admin_token, "rate-own")
    vid = await upload_video(client, admin_token, pid)
    lab_id, lab = await add_member(client, admin_token, pid, "annotator")
    _rev_id, rev = await add_member(client, admin_token, pid, "reviewer")

    wid = await _submit_one(client, admin_token, pid, vid, lab)
    await claim_review(client, rev, pid, wid)
    r = await client.post(
        f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev}"},
        json={"decision": "modify_and_approve",
              "modified_label_data": caption_payload("검수자가 고친 캡션입니다."),
              "base_label_id": await latest_label_id(client, rev, wid),
              "reason": "문장 1건 수정"},
    )
    assert r.status_code == 200, r.text

    rows = (await client.get(
        f"/projects/{pid}/dashboard/labeler-performance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    mine = next(x for x in rows if x["user_id"] == lab_id)
    assert mine["approved"] == 1, "수정 후 승인이 라벨러 승인에서 빠졌다"
    assert mine["submitted"] == 1, "제출 수가 흔들렸다"


async def test_reviewers_revision_is_not_their_submission(client: AsyncClient, admin_token: str):
    """검수자의 리비전을 그 검수자가 제출한 라벨로 세지 않는지 확인한다.

    리비전은 `submitted_by=검수자`로 저장된다.
    그대로 세면 라벨링을 한 적이 없는 검수자도 검수를 많이 할수록 제출 수가 늘어난다.
    """
    pid = await new_project(client, admin_token, "rate-rev")
    vid = await upload_video(client, admin_token, pid)
    _lab_id, lab = await add_member(client, admin_token, pid, "annotator")
    rev_id, rev = await add_member(client, admin_token, pid, "reviewer")

    wid = await _submit_one(client, admin_token, pid, vid, lab)
    await claim_review(client, rev, pid, wid)
    await client.post(
        f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev}"},
        json={"decision": "modify_and_approve",
              "modified_label_data": caption_payload("검수자가 고친 캡션입니다."),
              "base_label_id": await latest_label_id(client, rev, wid),
              "reason": "문장 1건 수정"},
    )

    rows = (await client.get(
        f"/projects/{pid}/dashboard/labeler-performance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    theirs = next(x for x in rows if x["user_id"] == rev_id)
    assert theirs["submitted"] == 0, "검수자의 리비전을 그 사람의 제출로 셌다"


async def test_plain_approve_is_unchanged(client: AsyncClient, admin_token: str):
    """일반 승인을 그대로 세는지 확인하는 대조군이다. 실패하면 일반 승인 집계까지 바뀌었다."""
    pid = await new_project(client, admin_token, "rate-plain")
    vid = await upload_video(client, admin_token, pid)
    lab_id, lab = await add_member(client, admin_token, pid, "annotator")
    _rev_id, rev = await add_member(client, admin_token, pid, "reviewer")

    wid = await _submit_one(client, admin_token, pid, vid, lab)
    await claim_review(client, rev, pid, wid)
    r = await client.post(
        f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev}"},
        json={"decision": "approve"},
    )
    assert r.status_code == 200, r.text
    rows = (await client.get(
        f"/projects/{pid}/dashboard/labeler-performance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    mine = next(x for x in rows if x["user_id"] == lab_id)
    assert (mine["submitted"], mine["approved"]) == (1, 1)
