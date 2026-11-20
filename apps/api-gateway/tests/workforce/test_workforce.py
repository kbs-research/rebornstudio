"""작업자 운영 테스트. 한 사람에 한 건씩 맡기는 다음 작업, 프로젝트 나가기, 멤버 제거를 확인한다.

동시 보유 한도는 없다. 이전 버전이 남긴 한도 값은 읽지 않는다.
"""

from uuid import UUID

from api_gateway.database import SessionLocal
from httpx import AsyncClient
from sqlalchemy import text
from tests._helpers import (
    add_member,
    new_project,
    seed_clip_work,
    upload_video,
)

# 다음 작업은 한 사람에 한 건이다. 이전 버전이 남긴 한도 값은 읽지 않는다

async def test_next_work_holds_one_item_and_ignores_old_limits(client: AsyncClient, admin_token: str):
    """다음 작업이 한 사람에게 한 건만 맡기고, 이전 버전이 남긴 한도 값(0 포함)을 읽지 않는지 확인한다.

    한 사람이 큐를 통째로 잡지 못하게 하는 것은 한도가 아니라 한 건씩 맡기는 규칙이다.
    이전 버전에서 관리자가 정한 한도 예외 `0`이 남아 있어도 작업이 막히지 않는다.
    """
    pid = await new_project(client, admin_token, "gate")
    v1 = await upload_video(client, admin_token, pid)
    v2 = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, v1)
    await seed_clip_work(pid, v2)
    uid, tok = await add_member(client, admin_token, pid, "annotator")
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE project_members SET max_concurrent_assignments=0 "
            "WHERE project_id=CAST(:p AS uuid) AND user_id=CAST(:u AS uuid)"
        ).bindparams(p=pid, u=uid))
        await s.execute(text(
            "UPDATE projects SET default_max_concurrent_assignments=1 WHERE project_id=CAST(:p AS uuid)"
        ).bindparams(p=pid))
        await s.commit()

    h = {"Authorization": f"Bearer {tok}"}
    r1 = await client.post(f"/projects/{pid}/work/next", headers=h)
    assert r1.status_code == 200 and r1.json()["resumed"] is False, r1.text
    r2 = await client.post(f"/projects/{pid}/work/next", headers=h)
    assert r2.status_code == 200, r2.text
    assert (r2.json()["work_item_id"], r2.json()["resumed"]) == (r1.json()["work_item_id"], True)
    c = (await client.get(f"/projects/{pid}/work/available-count", headers=h)).json()
    assert c == {"count": 1, "held": 1}, c


# 프로젝트 나가기: 마지막 관리자 가드

async def test_last_admin_cannot_leave_project(client: AsyncClient, admin_token: str):
    """마지막 project_admin이 프로젝트를 나갈 수 없는지(409) 확인한다.

    막는 대상이 마지막 관리자뿐인지도 함께 테스트로 보장한다.
    작업자는 본인 의지로 일찍 그만둘 수 있으므로 언제든 나갈 수 있어야 한다.
    규칙(`_ensure_other_admin_exists`)이 다른 사람을 강등·제거하는 경로에만 걸리고 자기가 나가는 경로에 없으면, 관리자가 혼자인 프로젝트에서 `프로젝트 나가기` 한 번으로 운영할 사람이 없는 프로젝트가 남는다.
    """
    pid = await new_project(client, admin_token, "leave")
    admin_h = {"Authorization": f"Bearer {admin_token}"}

    # 작업자는 언제든 나갈 수 있다.
    _, worker_tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.post(
        f"/projects/{pid}/me/leave",
        headers={"Authorization": f"Bearer {worker_tok}"},
        json={"reason": "일정이 안 맞습니다"},
    )
    assert r.status_code == 200, r.text

    # 프로젝트를 만든 사람이 유일한 관리자라서 나갈 수 없다.
    r = await client.post(f"/projects/{pid}/me/leave", headers=admin_h, json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "last_admin_cannot_leave"

    # 관리자가 한 명 더 있으면 나갈 수 있다.
    await add_member(client, admin_token, pid, "project_admin")
    r = await client.post(f"/projects/{pid}/me/leave", headers=admin_h, json={})
    assert r.status_code == 200, r.text


async def test_approval_rate_is_none_when_nothing_reviewed(
    client: AsyncClient, admin_token: str
):
    """검수된 건이 0이면 통과율이 0%가 아니라 `None`인지 확인한다.

    화면에서 `0%`는 승인이 하나도 없었다는 뜻으로 읽히지만, 실제로는 아직 아무것도 검수되지 않은 상태다.
    `rejection_rate`도 이 경우 `None`이지만 이유가 다르다.
    반려율의 분모는 작업량(제출)이고 이 멤버는 제출도 0이다.
    제출은 있는데 검수가 아직이면 반려율은 `0.0`, 통과율은 `None`이다(`test_dashboard`가 그 경우를 검사한다).
    """
    pid = await new_project(client, admin_token, "rate")
    admin_h = {"Authorization": f"Bearer {admin_token}"}
    uid, _tok = await add_member(client, admin_token, pid, "annotator")

    rows = (await client.get(
        f"/projects/{pid}/dashboard/labeler-performance", headers=admin_h
    )).json()
    row = next(x for x in rows if x["user_id"] == uid)
    assert row["approval_rate"] is None, row
    assert row["rejection_rate"] is None, row


# 멤버 제거: 나가기와 같은 결과여야 한다

async def test_removed_member_work_returns_to_queue(
    client: AsyncClient, admin_token: str
):
    """관리자가 멤버를 제거해도 가져간 작업이 큐로 돌아가는지 확인한다. 나가기와 같은 결과다.

    보유에는 끝나는 시각도 자동 회수도 없다. 제거가 보유를 풀지 않으면 그 작업은 제출할 수 없는 사람에게 계속 남는다.
    `/me/leave`와 `DELETE /members/{id}`가 다르게 처리하면 제거된 사람의 작업이 멤버가 아닌 사람을 가리킨 채 남는다.
    그러면 확인 대화상자의 안내가 사실과 달라진다.
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem
    from tests._helpers import seed_clip_work, upload_video

    pid = await new_project(client, admin_token, "rm-release")
    vid = await upload_video(client, admin_token, pid)
    _, item_id = await seed_clip_work(pid, vid)

    uid, tok = await add_member(client, admin_token, pid, "annotator")
    worker_h = {"Authorization": f"Bearer {tok}"}

    claimed = await client.post(f"/projects/{pid}/work/next", headers=worker_h)
    assert claimed.status_code == 200, claimed.text

    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(item_id))
        assert item.status == "claimed" and str(item.claimed_by) == uid

    r = await client.delete(f"/projects/{pid}/members/{uid}",
                            headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code in (200, 204), r.text

    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(item_id))
        assert item.status == "available", "제거했는데 작업이 아직 잡혀 있다"
        assert item.claimed_by is None
        assert item.draft is None, "다음 사람이 남의 초안을 받는다"
