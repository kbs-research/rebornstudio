"""반려가 세 번 쌓인 작업이 흐름은 그대로 두고 관리자 확인 대상으로만 표시되는지 보는 테스트."""
from httpx import AsyncClient
from tests._helpers import (
    add_member,
    caption_payload,
    claim_review,
    new_project,
    seed_clip_work,
    upload_video,
)


async def _submit_and_reject(client: AsyncClient, a: str, rev: str, pid: str, wid: str, n: int) -> None:
    """라벨러 제출과 검수자 반려를 n번 되풀이한다."""
    for i in range(n):
        r = await client.post(
            f"/work/{wid}/submit", headers={"Authorization": f"Bearer {a}"},
            json={"payload": caption_payload(f"{i + 1}차")},
        )
        assert r.status_code == 200, r.text
        await claim_review(client, rev, pid, wid)
        r = await client.post(
            f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev}"},
            json={"decision": "reject", "reason": f"{i + 1}차 반려", "reason_codes": ["caption_inaccurate"]},
        )
        assert r.status_code == 200, r.text


async def test_third_reject_keeps_rework_and_flags_for_admin(client: AsyncClient, admin_token: str):
    """세 번째 반려 뒤에도 작업이 만든 사람에게 재작업으로 남고, 목록·요약에 표시되는지 확인한다.

    두 번 반려된 작업은 목록에 들지 않는다. 목록의 행은 보유자와 마지막 반려 사유를 싣는다.
    """
    pid = await new_project(client, admin_token, "repeat-rej")
    _, a = await add_member(client, admin_token, pid, "annotator")
    _, b = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid, title="반복 영상")
    await seed_clip_work(pid, vid, start=0.0, end=5.0)
    await seed_clip_work(pid, vid, start=5.0, end=10.0)
    ha = {"Authorization": f"Bearer {a}"}
    hadm = {"Authorization": f"Bearer {admin_token}"}
    # 한 사람은 한 건씩 맡으므로 두 작업을 두 라벨러가 나눠 받는다.
    w3 = (await client.post(f"/projects/{pid}/work/next", headers=ha)).json()["work_item_id"]
    w2 = (await client.post(f"/projects/{pid}/work/next",
                            headers={"Authorization": f"Bearer {b}"})).json()["work_item_id"]
    await _submit_and_reject(client, a, rev, pid, w3, 3)
    await _submit_and_reject(client, b, rev, pid, w2, 2)

    item = (await client.get(f"/work/{w3}", headers=ha)).json()
    assert item["status"] == "claimed"
    assert item["rejection_count"] == 3

    summary = (await client.get(f"/projects/{pid}/pipeline/summary", headers=hadm)).json()
    assert summary["repeated_rejects"] == 1

    r = await client.get(f"/projects/{pid}/work/repeated-rejects", headers=hadm)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["threshold"] == 3
    assert page["total"] == 1
    [row] = page["items"]
    assert row["work_item_id"] == w3
    assert row["status"] == "claimed"
    assert row["video_title"] == "반복 영상"
    assert row["holder_name"] == "annotator"   # make_user가 역할 이름을 표시 이름으로 쓴다
    assert row["rejection_count"] == 3
    assert row["last_reject_comment"] == "3차 반려"
    assert [x["code"] for x in row["last_reject_reasons"]] == ["caption_inaccurate"]


async def test_repeated_rejects_list_is_closed_to_workers(client: AsyncClient, admin_token: str):
    """라벨러와 검수자는 반려 누적 목록을 받을 수 없는지 확인한다."""
    pid = await new_project(client, admin_token, "repeat-rej-authz")
    _, a = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    for tok in (a, rev):
        r = await client.get(
            f"/projects/{pid}/work/repeated-rejects", headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 403, r.text
