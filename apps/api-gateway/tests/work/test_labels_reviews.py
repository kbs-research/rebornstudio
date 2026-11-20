"""labels(리비전 체인)와 reviews INSERT 테스트."""

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import Label, Review
from httpx import AsyncClient
from sqlalchemy import select
from tests import _storage
from tests._helpers import (
    caption_payload,
    claim_review,
    invite_user,
    latest_label_id,
    make_account,
    make_user,
    put_video_file,
    seed_clip_work,
)
from tests.conftest import uniq


@pytest_asyncio.fixture(loop_scope="session")
async def isolated_project(client: AsyncClient, admin_token: str) -> dict:
    """격리 프로젝트와 멤버(라벨러·검수자)를 만든다."""
    slug = f"lab-{uniq('').split('@')[0]}"
    pid = (
        await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"slug": slug, "name": "lab test"},
        )
    ).json()["project_id"]
    await _storage.pin_project(pid)

    async def add_member(role: str) -> tuple[str, str]:
        """이 역할의 계정을 만들어 관리자 초대로 프로젝트에 넣고 (uid, token)을 돌려준다."""
        uid, email, pwd, _tok = await make_account(client, admin_token, role)
        await invite_user(client, admin_token, pid, uid, role)
        # 멤버십을 활성화한 뒤 역할을 반영하려고 토큰을 다시 발급한다.
        tok = (
            await client.post("/auth/login", json={"email": email, "password": pwd})
        ).json()["session"]["access_token"]
        return uid, tok

    _, annot_tok = await add_member("annotator")
    _, rev_tok = await add_member("reviewer")
    return {"project_id": pid, "annot_tok": annot_tok, "rev_tok": rev_tok}


async def _upload_pre_cleared_video(
    client: AsyncClient, admin_token: str, pid: str
) -> str:
    """pre_cleared 영상을 등록·업로드하고 video_id를 돌려준다."""
    # 응답 본문을 읽기 전에 상태 코드를 먼저 확인하고 실패 메시지에 응답을 싣는다.
    # `.json()["video_id"]`만 쓰면 `KeyError`만 나서 CI 로그로 403·409·422를 구별할 수 없다.
    r = await client.post(
        f"/projects/{pid}/videos",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"title": "lab-vid", "copyright_policy": "pre_cleared"},
    )
    assert r.status_code < 300, f"영상 생성 실패 {r.status_code}: {r.text[:300]}"
    vid = r.json()["video_id"]
    done = await put_video_file(client, admin_token, vid, b"x" * 80)
    assert done.status_code < 300, f"업로드 실패 {done.status_code}: {done.text[:300]}"
    return vid


async def test_submit_creates_label_with_parent_chain(
    client: AsyncClient, admin_token: str, isolated_project: dict
):
    """첫 제출의 parent가 NULL이고 재제출의 parent가 첫 Label.label_id인지 확인한다."""
    pid = isolated_project["project_id"]
    annot_tok = isolated_project["annot_tok"]
    rev_tok = isolated_project["rev_tok"]

    vid = await _upload_pre_cleared_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (
        await client.post(
            f"/projects/{pid}/work/next",
            headers={"Authorization": f"Bearer {annot_tok}"},
        )
    ).json()["work_item_id"]

    # 1차 제출
    await client.post(
        f"/work/{wid}/submit",
        headers={"Authorization": f"Bearer {annot_tok}"},
        json={"payload": caption_payload("처음 라벨")},
    )

    # 검수자가 반려하면 원 작업자에게 재작업으로 다시 배정한다.
    # 공용 풀로 돌려보내지 않는다.
    await claim_review(client, rev_tok, pid, wid)
    await client.post(
        f"/work/{wid}/review",
        headers={"Authorization": f"Bearer {rev_tok}"},
        json={"decision": "reject", "reason": "수정 필요"},
    )

    # 반려 클립은 원 작업자 큐에 재작업(claimed·재작업 1회)으로 남아야 한다.
    # 다시 가져올 필요가 없다.
    me = (
        await client.get("/work/me", headers={"Authorization": f"Bearer {annot_tok}"})
    ).json()
    mine = next((w for w in me if w["work_item_id"] == wid), None)
    assert mine is not None, "반려 클립이 원 작업자 큐에 남아야 함"
    assert mine["status"] == "claimed"
    assert mine["rejection_count"] == 1

    # 2차 제출(재작업)이다. 다시 가져오지 않고 바로 제출한다.
    await client.post(
        f"/work/{wid}/submit",
        headers={"Authorization": f"Bearer {annot_tok}"},
        json={"payload": caption_payload("수정된 라벨", tags=["news"])},
    )

    # DB에 labels가 2건 있고 두 번째의 parent가 첫 번째인지 확인한다.
    async with SessionLocal() as s:
        rows = (
            await s.scalars(
                select(Label)
                .where(Label.work_item_id == wid)
                .order_by(Label.submitted_at)
            )
        ).all()
    assert len(rows) == 2, f"labels 2건 기대, got {len(rows)}"
    assert rows[0].parent_label_id is None
    assert rows[1].parent_label_id == rows[0].label_id
    assert rows[1].diff_chars_count > 0


async def test_modify_and_approve_creates_new_label_revision(
    client: AsyncClient, admin_token: str, isolated_project: dict
):
    """검수자가 수정 후 승인하면 새 Label 리비전을 INSERT하고 parent 체인을 잇는지 확인한다."""
    pid = isolated_project["project_id"]
    annot_tok = isolated_project["annot_tok"]
    rev_tok = isolated_project["rev_tok"]

    vid = await _upload_pre_cleared_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (
        await client.post(
            f"/projects/{pid}/work/next",
            headers={"Authorization": f"Bearer {annot_tok}"},
        )
    ).json()["work_item_id"]
    await client.post(
        f"/work/{wid}/submit",
        headers={"Authorization": f"Bearer {annot_tok}"},
        json={"payload": caption_payload("annotator 원본")},
    )

    # reviewer가 직접 수정한 뒤 승인한다.
    await claim_review(client, rev_tok, pid, wid)
    r = await client.post(
        f"/work/{wid}/review",
        headers={"Authorization": f"Bearer {rev_tok}"},
        json={
            "decision": "modify_and_approve",
            "reason": "오타 수정",
            "modified_label_data": caption_payload("reviewer 수정본", tags=["news"]),
            "base_label_id": await latest_label_id(client, rev_tok, wid),
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    async with SessionLocal() as s:
        labels = (
            await s.scalars(
                select(Label)
                .where(Label.work_item_id == wid)
                .order_by(Label.submitted_at)
            )
        ).all()
        reviews = (
            await s.scalars(select(Review).where(Review.work_item_id == wid))
        ).all()
    assert len(labels) == 2, "원본 + reviewer 수정본 2개"
    assert labels[1].parent_label_id == labels[0].label_id
    assert labels[1].label_data["caption"]["brief"].startswith("reviewer 수정본")
    assert len(reviews) == 1
    assert reviews[0].verdict == "modify_and_approve"
    assert reviews[0].label_id == labels[1].label_id  # 새 리비전을 가리킨다.


async def test_modify_and_approve_requires_modified_data(
    client: AsyncClient, admin_token: str, isolated_project: dict
):
    """modified_label_data가 없으면 422를 주는지 확인한다."""
    pid = isolated_project["project_id"]
    annot_tok = isolated_project["annot_tok"]
    rev_tok = isolated_project["rev_tok"]

    vid = await _upload_pre_cleared_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (
        await client.post(
            f"/projects/{pid}/work/next",
            headers={"Authorization": f"Bearer {annot_tok}"},
        )
    ).json()["work_item_id"]
    await client.post(
        f"/work/{wid}/submit",
        headers={"Authorization": f"Bearer {annot_tok}"},
        json={"payload": caption_payload("검수")},
    )
    await claim_review(client, rev_tok, pid, wid)
    r = await client.post(
        f"/work/{wid}/review",
        headers={"Authorization": f"Bearer {rev_tok}"},
        json={"decision": "modify_and_approve"},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "modified_label_data_required"


async def test_modify_and_approve_rejects_edit_based_on_other_work(
    client: AsyncClient, admin_token: str, isolated_project: dict
):
    """다른 작업의 라벨을 기준으로 한 수정본은 409를, 기준 라벨이 없으면 422를 주는지 확인한다.

    검수 화면의 전체 수정본이 다음 건으로 넘어가면 그 건의 승인본으로 저장된다. 서버가 이 저장을 막는다.
    거절된 판정은 작업 상태와 라벨을 바꾸지 않는다.
    """
    pid = isolated_project["project_id"]
    annot_tok = isolated_project["annot_tok"]
    rev_tok = isolated_project["rev_tok"]
    annot = {"Authorization": f"Bearer {annot_tok}"}
    rev = {"Authorization": f"Bearer {rev_tok}"}

    vid = await _upload_pre_cleared_video(client, admin_token, pid)
    await seed_clip_work(pid, vid, start=0.0, end=5.0)
    await seed_clip_work(pid, vid, start=5.0, end=10.0)
    wids = []
    for note in ("앞 작업", "다음 작업"):
        r = await client.post(f"/projects/{pid}/work/next", headers=annot)
        assert r.status_code == 200, r.text
        wid = r.json()["work_item_id"]
        r = await client.post(
            f"/work/{wid}/submit", headers=annot, json={"payload": caption_payload(note)},
        )
        assert r.status_code == 200, r.text
        wids.append(wid)
    first, second = wids
    # 검수는 한 건씩 맡으므로 앞 작업을 판정한 뒤 다음 작업을 받는다.
    await claim_review(client, rev_tok, pid, first)
    first_label = await latest_label_id(client, rev_tok, first)
    r = await client.post(f"/work/{first}/review", headers=rev, json={"decision": "approve"})
    assert r.status_code == 200, r.text
    await claim_review(client, rev_tok, pid, second)
    second_label = await latest_label_id(client, rev_tok, second)
    leaked = {"caption": {"brief": "앞 작업에서 고친 캡션입니다."}}

    r = await client.post(
        f"/work/{second}/review", headers=rev,
        json={"decision": "modify_and_approve", "modified_label_data": leaked,
              "base_label_id": first_label},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "review_base_mismatch"

    r = await client.post(
        f"/work/{second}/review", headers=rev,
        json={"decision": "modify_and_approve", "modified_label_data": leaked},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "base_label_id_required"

    async with SessionLocal() as s:
        rows = (await s.scalars(select(Label).where(Label.work_item_id == second))).all()
        assert [str(x.label_id) for x in rows] == [second_label]
        assert (await s.scalars(select(Review).where(Review.work_item_id == second))).all() == []
    item = (await client.get(f"/work/{second}", headers=rev)).json()
    assert item["status"] == "submitted"

    # 본인 라벨을 기준으로 한 수정본은 받는다.
    r = await client.post(
        f"/work/{second}/review", headers=rev,
        json={"decision": "modify_and_approve",
              "modified_label_data": caption_payload("다음 작업을 고친 캡션입니다."),
              "base_label_id": second_label},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"


async def test_list_labels_endpoint(
    client: AsyncClient, admin_token: str, isolated_project: dict
):
    """GET /work/{id}/labels가 리비전 체인 순서대로 돌려주는지 확인한다."""
    pid = isolated_project["project_id"]
    annot_tok = isolated_project["annot_tok"]
    vid = await _upload_pre_cleared_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (
        await client.post(
            f"/projects/{pid}/work/next",
            headers={"Authorization": f"Bearer {annot_tok}"},
        )
    ).json()["work_item_id"]
    await client.post(
        f"/work/{wid}/submit",
        headers={"Authorization": f"Bearer {annot_tok}"},
        json={"payload": caption_payload("first")},
    )

    r = await client.get(
        f"/work/{wid}/labels",
        headers={"Authorization": f"Bearer {annot_tok}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    # 제출한 payload가 그대로 돌아온다. 캡션은 문장 개체를 담은 dict다.
    assert body[0]["label_data"]["caption"]["brief"].startswith("first ")
    assert body[0]["parent_label_id"] is None


async def test_list_labels_forbidden_for_outsider(
    client: AsyncClient, admin_token: str, isolated_project: dict
):
    """프로젝트 멤버가 아닌 일반 사용자가 403을 받는지 확인한다."""
    pid = isolated_project["project_id"]
    annot_tok = isolated_project["annot_tok"]
    vid = await _upload_pre_cleared_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (
        await client.post(
            f"/projects/{pid}/work/next",
            headers={"Authorization": f"Bearer {annot_tok}"},
        )
    ).json()["work_item_id"]

    # 어느 프로젝트에도 속하지 않은 outsider를 만든다.
    _uid, tok = await make_user(client, admin_token, "outsider")

    r = await client.get(
        f"/work/{wid}/labels", headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 403


async def test_review_creates_review_row_linked_to_label(
    client: AsyncClient, admin_token: str, isolated_project: dict
):
    """검수를 1회 하면 reviews에 1행이 생기는지 확인한다.

    그 행은 label_id로 라벨과 이어지고 verdict·comment를 보존한다.
    """
    pid = isolated_project["project_id"]
    annot_tok = isolated_project["annot_tok"]
    rev_tok = isolated_project["rev_tok"]

    vid = await _upload_pre_cleared_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (
        await client.post(
            f"/projects/{pid}/work/next",
            headers={"Authorization": f"Bearer {annot_tok}"},
        )
    ).json()["work_item_id"]
    await client.post(
        f"/work/{wid}/submit",
        headers={"Authorization": f"Bearer {annot_tok}"},
        json={"payload": caption_payload("검수 대상")},
    )
    await claim_review(client, rev_tok, pid, wid)
    await client.post(
        f"/work/{wid}/review",
        headers={"Authorization": f"Bearer {rev_tok}"},
        json={"decision": "approve", "reason": "good"},
    )

    async with SessionLocal() as s:
        reviews = (
            await s.scalars(select(Review).where(Review.work_item_id == wid))
        ).all()
        labels = (
            await s.scalars(select(Label).where(Label.work_item_id == wid))
        ).all()
    assert len(reviews) == 1
    assert len(labels) == 1
    assert reviews[0].verdict == "approve"
    assert reviews[0].comment == "good"
    assert reviews[0].label_id == labels[0].label_id


async def test_claim_next_review_exclusive(
    client: AsyncClient, admin_token: str, isolated_project: dict
):
    """다음 검수가 submitted 1건을 배타적으로 배정하고, 다시 부르면 같은 건을 다시 여는지 확인한다.

    검수자는 한 건씩 맡는다. 판정 전에는 새 검수를 받지 않는다.
    """
    pid = isolated_project["project_id"]
    annot_tok = isolated_project["annot_tok"]
    rev_tok = isolated_project["rev_tok"]
    vid = await _upload_pre_cleared_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    await seed_clip_work(pid, vid, start=5.0, end=10.0)
    annot = {"Authorization": f"Bearer {annot_tok}"}
    wids = []
    for _ in range(2):
        wid = (await client.post(f"/projects/{pid}/work/next", headers=annot)).json()["work_item_id"]
        await client.post(f"/work/{wid}/submit", headers=annot, json={"payload": caption_payload("검수")})
        wids.append(wid)
    rev = {"Authorization": f"Bearer {rev_tok}"}
    r = await client.post(f"/projects/{pid}/review/next", headers=rev)
    assert r.status_code == 200, r.text
    assert (r.json()["work_item_id"], r.json()["resumed"]) == (wids[0], False)
    item = (await client.get(f"/work/{wids[0]}", headers=rev)).json()
    assert item["review_claimed_by"] is not None
    # 대기 중인 검수가 남아 있어도 보유한 검수를 다시 연다.
    r2 = await client.post(f"/projects/{pid}/review/next", headers=rev)
    assert r2.status_code == 200, r2.text
    assert (r2.json()["work_item_id"], r2.json()["resumed"]) == (wids[0], True)


async def test_review_mine_lists_the_held_review(
    client: AsyncClient, admin_token: str, isolated_project: dict
):
    """다음 검수로 받은 작업이 /review/mine에 나타나고 판정하면 빠지는지 확인한다. 보유에는 끝나는 시각이 없다."""
    pid = isolated_project["project_id"]
    annot_tok = isolated_project["annot_tok"]
    rev_hdr = {"Authorization": f"Bearer {isolated_project['rev_tok']}"}
    vid = await _upload_pre_cleared_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (await client.post(
        f"/projects/{pid}/work/next", headers={"Authorization": f"Bearer {annot_tok}"}
    )).json()["work_item_id"]
    await client.post(
        f"/work/{wid}/submit", headers={"Authorization": f"Bearer {annot_tok}"},
        json={"payload": caption_payload("검수")},
    )
    await client.post(f"/projects/{pid}/review/next", headers=rev_hdr)
    mine = (await client.get(f"/projects/{pid}/review/mine", headers=rev_hdr)).json()
    assert [m["work_item_id"] for m in mine] == [wid]
    assert "review_expires_at" not in mine[0]
    # 판정하면 보유가 풀려 /review/mine이 빈다.
    r = await client.post(f"/work/{wid}/review", headers=rev_hdr, json={"decision": "approve"})
    assert r.status_code == 200, r.text
    assert (await client.get(f"/projects/{pid}/review/mine", headers=rev_hdr)).json() == []


async def test_rework_is_not_reclaimed_and_comes_back_to_its_author(
    client: AsyncClient, admin_token: str
):
    """반려된 재작업이 원 작업자의 보유로 남고, 다음 작업이 그 재작업을 먼저 여는지 확인한다.

    보유에는 끝나는 시각과 자동 회수가 없다. 이전 버전이 남긴 지난 만료 시각이 있어도 재작업은 원 작업자에게 남는다.
    풀로 돌아간 재작업도 원 작업자가 다음 작업을 누르면 먼저 받는다.
    """
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem
    from sqlalchemy import update
    from tests._helpers import add_member, new_project, seed_clip_work, upload_video

    pid = await new_project(client, admin_token, "rework-keep")
    uid, annot = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    ah = {"Authorization": f"Bearer {annot}"}

    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (await client.post(f"/projects/{pid}/work/next", headers=ah)).json()["work_item_id"]
    await client.post(f"/work/{wid}/submit", headers=ah, json={"payload": caption_payload("검수")})
    # 제출 뒤 라벨러는 다음 작업을 받는다. 반려가 오면 두 건을 잠시 보유한다.
    await seed_clip_work(pid, vid, start=5.0, end=10.0)
    fresh = (await client.post(f"/projects/{pid}/work/next", headers=ah)).json()
    assert fresh["work_item_id"] != wid and fresh["resumed"] is False
    await claim_review(client, rev, pid, wid)
    await client.post(f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev}"},
                      json={"decision": "reject", "reason": "다시"})

    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(wid))
        assert item.status == "claimed" and item.rejection_count == 1
        assert str(item.claimed_by) == uid and item.expires_at is None
        item.expires_at = datetime.now(UTC) - timedelta(days=1)
        await s.commit()

    # 다음 작업은 반려로 돌아온 재작업을 새 작업보다 먼저 연다.
    again = (await client.post(f"/projects/{pid}/work/next", headers=ah)).json()
    assert (again["work_item_id"], again["resumed"]) == (wid, True)

    # 재작업은 지난 만료 시각과 관계없이 제출할 수 있다.
    r = await client.post(f"/work/{wid}/submit", headers=ah, json={"payload": caption_payload("고침")})
    assert r.status_code == 200, r.text

    # 풀로 돌아가더라도 원 작업자가 다시 받으면 그 건이 온다.
    async with SessionLocal() as s:
        await s.execute(
            update(WorkItem)
            .where(WorkItem.work_item_id == UUID(wid))
            .values(status="available", claimed_by=None, expires_at=None)
        )
        await s.execute(
            update(WorkItem)
            .where(WorkItem.work_item_id == UUID(fresh["work_item_id"]))
            .values(status="available", claimed_by=None)
        )
        await s.commit()
    back = (await client.post(f"/projects/{pid}/work/next", headers=ah)).json()
    assert back["work_item_id"] == wid, "내가 냈던 건이 먼저 와야 한다"


# ── 검수 보유 게이트 ───────────────────────────────────────
#
# 판정·반납·초안 저장은 검수를 보유한 사람만 할 수 있다.
# 아래 테스트는 이 조건이 세 동작 모두에 걸려 있는지 확인한다.
# 화면(`review.tsx`의 `heldByMe`)만 보유자를 허용하고 서버가 확인하지 않으면,
# 보유가 넘어간 뒤 이전 탭에서 판정할 때 새 보유자의 초안이 지워진다.


async def _submitted_item(client: AsyncClient, admin_token: str) -> dict:
    """라벨러가 제출한 작업 하나와 검수자 둘을 만든다. 보유 게이트 테스트에 쓴다."""
    from tests._helpers import add_member, new_project, upload_video

    pid = await new_project(client, admin_token, "rv-hold")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    _, rev_a = await add_member(client, admin_token, pid, "reviewer")
    _, rev_b = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    wid = (await client.post(f"/projects/{pid}/work/next",
                             headers={"Authorization": f"Bearer {annot}"})).json()["work_item_id"]
    r = await client.post(f"/work/{wid}/submit", headers={"Authorization": f"Bearer {annot}"},
                          json={"payload": caption_payload("검수 대상")})
    assert r.status_code == 200, r.text
    return {"pid": pid, "wid": wid, "annot": annot, "rev_a": rev_a, "rev_b": rev_b}


async def test_decide_requires_holding_the_review_claim(
    client: AsyncClient, admin_token: str
):
    """검수를 보유하지 않은 검수자의 판정이 403인지, 아무도 가져가지 않은 건도 같은지 확인한다."""
    x = await _submitted_item(client, admin_token)
    a = {"Authorization": f"Bearer {x['rev_a']}"}
    b = {"Authorization": f"Bearer {x['rev_b']}"}

    # 아무도 가져가지 않은 건이다. 판정하려면 먼저 가져와야 한다.
    r = await client.post(f"/work/{x['wid']}/review", headers=b, json={"decision": "approve"})
    assert r.status_code == 403 and r.json()["detail"] == "not_your_review_claim", r.text

    await claim_review(client, x["rev_a"], x["pid"], x["wid"])
    # A가 보유 중이다. B의 판정은 막히고 건은 제출 상태로 남는다.
    r = await client.post(f"/work/{x['wid']}/review", headers=b,
                          json={"decision": "reject", "reason": "남의 판정"})
    assert r.status_code == 403 and r.json()["detail"] == "not_your_review_claim", r.text
    async with SessionLocal() as s:
        from uuid import UUID

        from api_gateway.models import WorkItem
        item = await s.get(WorkItem, UUID(x["wid"]))
        assert item.status == "submitted" and item.rejection_count == 0

    # 보유자의 판정은 통과한다.
    r = await client.post(f"/work/{x['wid']}/review", headers=a, json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "approved", r.text


async def test_decide_twice_is_409_not_a_second_review(
    client: AsyncClient, admin_token: str
):
    """같은 판정을 두 번 보내면 두 번째가 409이고 판정 기록이 둘이 되지 않는지 확인한다."""
    x = await _submitted_item(client, admin_token)
    a = {"Authorization": f"Bearer {x['rev_a']}"}
    await claim_review(client, x["rev_a"], x["pid"], x["wid"])
    r1 = await client.post(f"/work/{x['wid']}/review", headers=a, json={"decision": "approve"})
    assert r1.status_code == 200, r1.text
    r2 = await client.post(f"/work/{x['wid']}/review", headers=a, json={"decision": "approve"})
    assert r2.status_code == 409 and r2.json()["detail"] == "not_submitted_approved", r2.text
    async with SessionLocal() as s:
        reviews = (await s.scalars(select(Review).where(Review.work_item_id == x["wid"]))).all()
    assert len(reviews) == 1


async def test_review_draft_holder_only_and_not_after_the_decision(
    client: AsyncClient, admin_token: str
):
    """검수 초안 저장이 보유자 200, 다른 사람 403, 판정 뒤 409를 받는지 확인한다.

    화면은 저장 실패를 저장됨으로 표시하지 않고 실패 응답 코드로 분기한다.
    403이면 보유를 잃었으므로 읽기 전용으로 바꾼다. 409면 이미 판정됐다.
    """
    x = await _submitted_item(client, admin_token)
    a = {"Authorization": f"Bearer {x['rev_a']}"}
    b = {"Authorization": f"Bearer {x['rev_b']}"}
    await claim_review(client, x["rev_a"], x["pid"], x["wid"])

    r = await client.patch(f"/work/{x['wid']}/review/draft", headers=a, json={"extra": "메모"})
    assert r.status_code == 200 and r.json()["review_draft"] == {"extra": "메모"}, r.text

    r = await client.patch(f"/work/{x['wid']}/review/draft", headers=b, json={"extra": "남의 것"})
    assert r.status_code == 403 and r.json()["detail"] == "not_your_review_claim", r.text

    assert (await client.post(f"/work/{x['wid']}/review", headers=a,
                              json={"decision": "approve"})).status_code == 200
    # 판정 뒤에 늦게 도착한 디바운스 저장이다. 판정이 비운 초안을 되살리지 않는다.
    r = await client.patch(f"/work/{x['wid']}/review/draft", headers=a, json={"extra": "늦은 저장"})
    assert r.status_code == 409 and r.json()["detail"] == "not_submitted_approved", r.text
    assert (await client.get(f"/work/{x['wid']}", headers=a)).json()["review_draft"] is None


async def test_concurrent_next_review_by_one_person_assigns_one(
    client: AsyncClient, admin_token: str
):
    """같은 사람이 다음 검수를 동시에 세 번 눌러도 한 건만 배정되는지 확인한다.

    서버는 라벨링과 같이 멤버 행을 잠가 요청을 차례로 처리한다.
    잠그지 않으면 보유를 확인한 뒤 점유하기 전의 틈에 세 요청이 겹쳐 세 건이 배정된다.
    """
    import asyncio
    from uuid import UUID

    from api_gateway.models import WorkItem
    from tests._helpers import add_member, new_project, upload_video

    pid = await new_project(client, admin_token, "rv-race")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    rev_uid, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    for i in range(3):
        await seed_clip_work(pid, vid, start=i * 5.0, end=i * 5.0 + 5.0)
        wid = (await client.post(f"/projects/{pid}/work/next",
                                 headers={"Authorization": f"Bearer {annot}"})).json()["work_item_id"]
        await client.post(f"/work/{wid}/submit", headers={"Authorization": f"Bearer {annot}"},
                          json={"payload": caption_payload("검수")})

    hdr = {"Authorization": f"Bearer {rev}"}
    # 커넥션 풀을 미리 채운다.
    # 채우지 않으면 두 번째 요청이 새 커넥션을 여는 동안 첫 요청이 커밋까지 끝난다.
    # 그러면 잠금이 없어도 요청이 차례로 처리되어 이 테스트가 결함을 잡지 못한다.
    await asyncio.gather(*[
        client.get(f"/projects/{pid}/review/mine", headers=hdr) for _ in range(4)
    ])
    results = await asyncio.gather(*[
        client.post(f"/projects/{pid}/review/next", headers=hdr) for _ in range(3)
    ])
    assert [r.status_code for r in results] == [200, 200, 200], [r.text[:120] for r in results]
    assert len({r.json()["work_item_id"] for r in results}) == 1
    assert sorted(r.json()["resumed"] for r in results) == [False, True, True]
    async with SessionLocal() as s:
        held = (await s.scalars(select(WorkItem).where(
            WorkItem.project_id == UUID(pid), WorkItem.review_claimed_by == UUID(rev_uid),
        ))).all()
    assert len(held) == 1, f"한 건만 맡아야 하는데 {len(held)}건을 보유했다"


async def test_qc_endpoint_null_run_and_outsider(
    client: AsyncClient, admin_token: str
):
    """`GET /work/{id}/qc`가 세 가지 응답을 구별해 주는지 확인한다.

    QC가 돌지 않았으면 200 `state=none`, 돌았으면 `recorded`와 그 실행, 프로젝트 밖 사람이면 403이다.
    화면은 조회 실패와 `none`(돌지 않음)을 다르게 그려야 하므로 서버 응답의 상태 값으로 구별한다.
    `none`이 오류나 빈 실행으로 바뀌면 화면이 잘못된 분기로 간다.
    """
    from uuid import UUID

    from api_gateway.models import CaptionQcRun
    from tests._helpers import make_user

    x = await _submitted_item(client, admin_token)
    a = {"Authorization": f"Bearer {x['rev_a']}"}

    # 프로젝트가 QC를 켜지 않았다. 기록이 없으므로 서버는 `none`을 돌려준다.
    r = await client.get(f"/work/{x['wid']}/qc", headers=a)
    assert r.status_code == 200 and r.json() == {"state": "none", "run": None}, r.text

    async with SessionLocal() as s:
        s.add(CaptionQcRun(
            work_item_id=UUID(x["wid"]), trigger="submit", verdict="needs_review",
            findings=[{"check": "sentence_ending", "severity": "warn",
                       "message": "문장이 종결되지 않았습니다"}],
            adapter_key="rules", stages=[{"key": "caption", "status": "passed"}],
        ))
        await s.commit()
    r = await client.get(f"/work/{x['wid']}/qc", headers=a)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "recorded"
    run = r.json()["run"]
    assert run["verdict"] == "needs_review" and len(run["findings"]) == 1

    # 프로젝트 밖 사람이다. QC 근거도 그 작업을 볼 수 있는 사람만 본다.
    _, outsider = await make_user(client, admin_token, "qc-out")
    r = await client.get(f"/work/{x['wid']}/qc", headers={"Authorization": f"Bearer {outsider}"})
    assert r.status_code == 403, r.text
