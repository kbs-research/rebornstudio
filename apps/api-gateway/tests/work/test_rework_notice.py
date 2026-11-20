"""반려된 작업이 내 작업 목록에서 마지막 반려 사유와 함께 보이는지 보는 테스트.

알림이 없으므로 라벨러는 내 작업 목록의 재작업 행으로 반려를 알게 된다.
"""
from httpx import AsyncClient
from tests._helpers import (
    add_member,
    caption_payload,
    claim_review,
    new_project,
    seed_clip_work,
    upload_video,
)


async def _reject(client: AsyncClient, rev: str, pid: str, wid: str) -> None:
    """검수를 가져와 사유 칩 하나와 문장으로 반려한다."""
    await claim_review(client, rev, pid, wid)
    r = await client.post(
        f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev}"},
        json={"decision": "reject", "reason": "점검", "reason_codes": ["caption_inaccurate"]},
    )
    assert r.status_code == 200, r.text


async def test_my_work_row_carries_the_last_reject_reasons(client: AsyncClient, admin_token: str):
    """내 작업 목록의 재작업 행이 마지막 반려의 사유 코드와 이름을 싣고 새 작업은 빈 목록인지 확인한다.

    `재작업 N회`만 보이면 라벨러는 무엇을 고칠지 작업을 열어야 알 수 있다.
    """
    pid = await new_project(client, admin_token, "my-rework")
    _, a = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid, start=0.0, end=5.0)
    await seed_clip_work(pid, vid, start=5.0, end=10.0)
    ha = {"Authorization": f"Bearer {a}"}
    wid = (await client.post(f"/projects/{pid}/work/next", headers=ha)).json()["work_item_id"]
    await client.post(f"/work/{wid}/submit", headers=ha, json={"payload": caption_payload("1차")})
    await _reject(client, rev, pid, wid)
    await client.post(f"/work/{wid}/submit", headers=ha, json={"payload": caption_payload("2차")})
    # 제출 뒤에는 보유가 없으므로 다음 작업이 새 작업을 준다.
    fresh = (await client.post(f"/projects/{pid}/work/next", headers=ha)).json()["work_item_id"]
    assert fresh != wid
    await claim_review(client, rev, pid, wid)
    r = await client.post(
        f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev}"},
        json={"decision": "reject", "reason": "다시", "reason_codes": ["caption_inaccurate"]},
    )
    assert r.status_code == 200, r.text

    mine = {w["work_item_id"]: w for w in (await client.get("/work/me", headers=ha)).json()}
    assert mine[wid]["rejection_count"] == 2
    assert mine[wid]["last_reject_reasons"] == [
        {"code": "caption_inaccurate", "label": "캡션 부정확"},
    ]
    assert mine[fresh]["last_reject_reasons"] == []
