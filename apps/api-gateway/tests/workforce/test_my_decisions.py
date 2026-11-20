"""검수자 본인의 판정 목록 조회 테스트.

판정 목록만 준다. 수·비율·소요 시간 같은 지표는 싣지 않는다.
최신순 커서가 빠짐없이, 겹침 없이 이어지는지와 `total`이 페이지 수가 아니라 전체 수인지 확인한다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from api_gateway.database import SessionLocal
from api_gateway.models import Review
from httpx import AsyncClient
from tests._helpers import add_member, new_project, seed_clip_work, upload_video

_T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _h(tok: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _seed(client: AsyncClient, admin_token: str, name: str) -> dict:
    """검수 판정을 시드한다. 한 검수자의 판정이 이 프로젝트에 세 건, 다른 프로젝트에 한 건 있다.

    시각 순서는 반려(t0), 승인(t1), 수정 후 승인(t2)이고 다른 프로젝트의 승인은 t3이다.
    """
    pid = await new_project(client, admin_token, name)
    rev_id, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid, title="판정 대상")
    c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=5.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=5.0, end=12.0)
    _c3, w3 = await seed_clip_work(pid, vid, start=12.0, end=20.0)

    other = await new_project(client, admin_token, f"{name}-other")
    ovid = await upload_video(client, admin_token, other)
    _oc, ow = await seed_clip_work(other, ovid)

    async with SessionLocal() as s:
        s.add(Review(work_item_id=UUID(w1), reviewer_id=UUID(rev_id), verdict="reject",
                     reason_codes=["speaker_name"], comment="화자 확인", reviewed_at=_T0))
        s.add(Review(work_item_id=UUID(w2), reviewer_id=UUID(rev_id), verdict="approve",
                     reviewed_at=_T0 + timedelta(hours=1)))
        s.add(Review(work_item_id=UUID(w3), reviewer_id=UUID(rev_id),
                     verdict="modify_and_approve", reviewed_at=_T0 + timedelta(hours=2)))
        s.add(Review(work_item_id=UUID(ow), reviewer_id=UUID(rev_id), verdict="approve",
                     reviewed_at=_T0 + timedelta(hours=3)))
        await s.commit()
    return {"pid": pid, "vid": vid, "c1": c1, "w1": w1, "rev_id": rev_id, "rev": rev}


async def test_my_decisions_newest_first_within_the_project(client: AsyncClient, admin_token: str):
    """본인 판정을 최신순으로 싣고 다른 프로젝트 판정은 빼는지 확인한다."""
    d = await _seed(client, admin_token, "dec-list")
    r = await client.get(f"/projects/{d['pid']}/me/decisions", headers=_h(d["rev"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert [x["decision"] for x in body["items"]] == ["modify_and_approve", "approve", "reject"]
    reject = body["items"][-1]
    assert reject["work_item_id"] == d["w1"] and reject["clip_id"] == d["c1"]
    assert (reject["clip_start_seconds"], reject["clip_end_seconds"]) == (0.0, 5.0)
    assert reject["video_title"] == "판정 대상"
    assert reject["reasons"] == [{"code": "speaker_name", "label": "화자명 오류"}]
    assert reject["comment"] == "화자 확인"
    # 지표는 싣지 않는다.
    assert set(body) == {"total", "items"}


async def test_my_decisions_cursor_walks_every_row_once(client: AsyncClient, admin_token: str):
    """한 건씩 넘겨도 세 건을 빠짐없이, 겹침 없이 받고 `total`이 페이지마다 같은지 확인한다."""
    d = await _seed(client, admin_token, "dec-cursor")
    url = f"/projects/{d['pid']}/me/decisions"
    seen: list[str] = []
    params: dict = {"limit": 1}
    for _ in range(5):
        body = (await client.get(url, headers=_h(d["rev"]), params=params)).json()
        assert body["total"] == 3
        if not body["items"]:
            break
        last = body["items"][-1]
        seen.append(last["decision_id"])
        params = {"limit": 1, "before": last["decided_at"], "before_id": last["decision_id"]}
    assert len(seen) == 3 and len(set(seen)) == 3


async def test_my_decisions_shows_only_own_rows(client: AsyncClient, admin_token: str):
    """판정이 없는 멤버는 0건, 비멤버는 403, 없는 프로젝트는 404인지 확인한다.

    경로에 사용자 ID가 없으므로 다른 사람의 판정은 볼 수 없다.
    """
    from tests._helpers import make_user

    d = await _seed(client, admin_token, "dec-mine")
    url = f"/projects/{d['pid']}/me/decisions"

    _, lab = await add_member(client, admin_token, d["pid"], "annotator")
    assert (await client.get(url, headers=_h(lab))).json() == {"total": 0, "items": []}

    _, outsider = await make_user(client, admin_token, "dec-out")
    assert (await client.get(url, headers=_h(outsider))).status_code == 403

    missing = await client.get(f"/projects/{uuid4()}/me/decisions", headers=_h(d["rev"]))
    assert missing.status_code == 404
