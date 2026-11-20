"""작업의 전체 생애 주기(다음 작업, 제출, 검수 승인)와 실패 경로 테스트."""
import pytest_asyncio
from httpx import AsyncClient
from tests import _storage
from tests._helpers import (
    caption_payload,
    claim_review,
    invite_user,
    latest_label_id,
    make_account,
    put_video_file,
    seed_clip_work,
)
from tests.conftest import uniq


@pytest_asyncio.fixture(loop_scope="session")
async def isolated_project(
    client: AsyncClient, admin_token: str
) -> dict:
    """새 프로젝트와 멤버(annotator, reviewer)를 준비한다."""
    slug = f"wk-{uniq('').split('@')[0]}"
    pid = (
        await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"slug": slug, "name": "work test"},
        )
    ).json()["project_id"]
    await _storage.pin_project(pid)

    async def add_member(role: str) -> tuple[str, str]:
        """계정을 만들고 관리자 초대로 프로젝트에 넣은 뒤 (uid, 토큰)을 돌려준다."""
        uid, email, pwd, _first_tok = await make_account(client, admin_token, role)
        await invite_user(client, admin_token, pid, uid, role)
        token = (
            await client.post("/auth/login", json={"email": email, "password": pwd})
        ).json()["session"]["access_token"]
        return uid, token

    annot_uid, annot_tok = await add_member("annotator")
    rev_uid, rev_tok = await add_member("reviewer")
    return {
        "project_id": pid,
        "annotator_token": annot_tok,
        "reviewer_token": rev_tok,
    }


async def _upload_video(client, admin_token, pid, title):
    """영상을 만들고 멀티파트로 올린 뒤 업로드를 완료한다."""
    vid = (
        await client.post(
            f"/projects/{pid}/videos",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": title, "copyright_policy": "pre_cleared"},
        )
    ).json()["video_id"]
    await put_video_file(client, admin_token, vid, b"x" * 100)
    return vid


async def test_claim_submit_review_approve(
    client: AsyncClient, admin_token: str, isolated_project: dict
):
    """라벨러가 가져와 제출한 작업을 검수자가 대기 수로 확인하고 가져와 승인할 수 있는지 확인한다."""
    pid = isolated_project["project_id"]
    annot = isolated_project["annotator_token"]
    rev = isolated_project["reviewer_token"]

    vid = await _upload_video(client, admin_token, pid, "wk-v1")
    await seed_clip_work(pid, vid)

    # 가져오기
    r = await client.post(
        f"/projects/{pid}/work/next",
        headers={"Authorization": f"Bearer {annot}"},
    )
    assert r.status_code == 200
    wid = r.json()["work_item_id"]

    # 제출
    r = await client.post(
        f"/work/{wid}/submit",
        headers={"Authorization": f"Bearer {annot}"},
        json={"payload": caption_payload("테스트")},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "submitted"

    # reviewer의 검수 대기 수에 잡힌다.
    r = await client.get(
        f"/projects/{pid}/review/available-count",
        headers={"Authorization": f"Bearer {rev}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1

    # 승인한다. 판정은 검수를 보유한 사람만 한다.
    await claim_review(client, rev, pid, wid)
    r = await client.post(
        f"/work/{wid}/review",
        headers={"Authorization": f"Bearer {rev}"},
        json={"decision": "approve"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


async def test_submit_consumes_draft(
    client: AsyncClient, admin_token: str, isolated_project: dict
):
    """제출이 임시저장(draft)을 비우는지 확인한다.

    반려 후 재작업으로 다시 들어올 때 캡션은 직전 제출 라벨에서 복원해야 한다.
    draft가 남으면 오래된 draft(예: AI 시드)가 복원된다.
    """
    pid = isolated_project["project_id"]
    annot = isolated_project["annotator_token"]
    vid = await _upload_video(client, admin_token, pid, "wk-draft")
    await seed_clip_work(pid, vid)

    wid = (
        await client.post(
            f"/projects/{pid}/work/next",
            headers={"Authorization": f"Bearer {annot}"},
        )
    ).json()["work_item_id"]

    # 임시저장(draft)을 채우고 draft가 있는지 확인한다.
    r = await client.patch(
        f"/work/{wid}/draft",
        headers={"Authorization": f"Bearer {annot}"},
        json={"payload": {"caption": {"brief": "임시 초안"}}},
    )
    assert r.status_code == 200
    assert r.json()["draft"] is not None

    # 제출하면 draft가 null이 된다.
    r = await client.post(
        f"/work/{wid}/submit",
        headers={"Authorization": f"Bearer {annot}"},
        json={"payload": caption_payload("최종 제출")},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "submitted"
    assert r.json()["draft"] is None


async def test_claim_when_no_work_available(
    client: AsyncClient, admin_token: str
):
    """가져올 작업이 없으면 가져오기가 404를 주는지 확인한다."""
    # 작업이 없는 빈 프로젝트를 만든다.
    pid = (
        await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"slug": f"empty-{uniq('').split('@')[0]}",
                  "name": "empty"},
        )
    ).json()["project_id"]
    await _storage.pin_project(pid)
    # admin은 project_admin 권한이 있으므로 직접 claim한다.
    r = await client.post(
        f"/projects/{pid}/work/next",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404


async def test_export_empty_project(
    client: AsyncClient, admin_token: str
):
    """빈 프로젝트도 내보내기를 큐에 넣고 워커가 처리하는지 확인한다."""
    pid = (
        await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"slug": f"exp-{uniq('').split('@')[0]}",
                  "name": "exp"},
        )
    ).json()["project_id"]
    await _storage.pin_project(pid)
    r = await client.post(
        f"/projects/{pid}/exports",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 201
    assert r.json()["status"] == "pending"  # 비동기 큐에 들어간다.
    # 워커 동작을 흉내 낸다.
    from api_gateway.database import SessionLocal
    from api_gateway.services.exports.export_service import process_pending
    async with SessionLocal() as s:
        assert await process_pending(s, limit=10) >= 1


# ── 제출·저장의 실패 경로 ─────────────────────────────────────────
#
# 화면은 오류 코드마다 다른 문구를 띄운다(`shared/api/error_text.ts`).
# 그래서 응답의 오류 코드가 화면과의 계약이다. 코드가 바뀌면 화면이 엉뚱한 문구를 띄운다.


async def _claimed(client: AsyncClient, admin_token: str) -> dict:
    """라벨러 둘과, 그중 한 명이 가져간 작업 하나를 준비한다."""
    from tests._helpers import add_member, new_project, upload_video

    pid = await new_project(client, admin_token, "wk-fail")
    a_uid, a = await add_member(client, admin_token, pid, "annotator")
    _, b = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    r = await client.post(f"/projects/{pid}/work/next", headers={"Authorization": f"Bearer {a}"})
    assert r.status_code == 200, r.text
    return {"pid": pid, "wid": r.json()["work_item_id"], "a": a, "a_uid": a_uid, "b": b}


async def test_submitting_twice_is_409(client: AsyncClient, admin_token: str):
    """두 번째 제출이 409 `work_item_is_submitted`를 받고 라벨이 둘이 되지 않는지 확인한다."""
    x = await _claimed(client, admin_token)
    hdr = {"Authorization": f"Bearer {x['a']}"}
    body = {"payload": caption_payload("첫 제출")}
    assert (await client.post(f"/work/{x['wid']}/submit", headers=hdr, json=body)).status_code == 200
    r = await client.post(f"/work/{x['wid']}/submit", headers=hdr, json=body)
    assert r.status_code == 409 and r.json()["detail"] == "work_item_is_submitted", r.text


async def test_submit_after_someone_else_took_it_is_403(
    client: AsyncClient, admin_token: str
):
    """프로젝트를 나가 다른 사람이 가져간 작업에 이전 탭이 제출하면 403 `not_your_claim`을 받는지 확인한다."""
    x = await _claimed(client, admin_token)
    a = {"Authorization": f"Bearer {x['a']}"}
    r = await client.post(f"/projects/{x['pid']}/me/leave", headers=a, json={"reason": None})
    assert r.status_code == 200 and r.json()["released"] == 1, r.text
    r = await client.post(f"/projects/{x['pid']}/work/next",
                          headers={"Authorization": f"Bearer {x['b']}"})
    assert r.status_code == 200 and r.json()["work_item_id"] == x["wid"], r.text

    r = await client.post(f"/work/{x['wid']}/submit", headers=a,
                          json={"payload": caption_payload("옛 탭")})
    assert r.status_code == 403 and r.json()["detail"] == "not_your_claim", r.text


async def test_old_expiry_does_not_block_submit_or_draft(
    client: AsyncClient, admin_token: str
):
    """이전 버전이 남긴 지난 만료 시각이 있어도 보유자의 저장과 제출이 되고 다른 사람에게 가지 않는지 확인한다.

    보유에는 끝나는 시각이 없다. 제출하거나 신고할 때까지 보유자의 작업이다.
    """
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import WorkItem

    x = await _claimed(client, admin_token)
    a = {"Authorization": f"Bearer {x['a']}"}
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(x["wid"]))
        item.expires_at = datetime.now(UTC) - timedelta(days=3)
        await s.commit()

    # 다른 사람은 그 작업을 받지 않는다. 대기열에 다른 작업이 없으므로 404다.
    r = await client.post(f"/projects/{x['pid']}/work/next",
                          headers={"Authorization": f"Bearer {x['b']}"})
    assert r.status_code == 404 and r.json()["detail"] == "no_work_available", r.text

    r = await client.patch(f"/work/{x['wid']}/draft", headers=a,
                           json={"payload": {"caption": {"brief": "쓰는 중"}}})
    assert r.status_code == 200, r.text
    r = await client.post(f"/work/{x['wid']}/submit", headers=a,
                          json={"payload": caption_payload("시간이 지나도 제출")})
    assert r.status_code == 200 and r.json()["status"] == "submitted", r.text


def test_work_error_codes_are_in_the_screen_dictionary():
    """작업·검수 서버가 내는 오류 코드가 모두 화면 사전(`shared/api/error_text.ts`)에 있는지 확인한다.

    사전에 없는 코드는 화면이 일반 문구(`처리하지 못했습니다`)로 보여 준다.
    그러면 원인을 아는 오류도 사용자에게 원인을 알리지 못한다.
    서버에 새 코드를 더하면서 사전을 빠뜨리는 실수는 `tsc`도 다른 테스트도 잡지 못한다.
    그래서 이 테스트가 소스를 읽어 확인한다.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    work = root / "apps/api-gateway/api_gateway/services/work"
    # 큐 코어는 패키지(`work/service/`)라 하위 모듈을 모두 읽는다.
    sources = [*sorted((work / "service").glob("*.py")), work / "review.py"]
    assert len(sources) > 2, "큐 코어 하위 모듈을 못 찾았다 — 경로가 바뀌었으면 이 시험도 고친다"
    codes: set[str] = set()
    for src in sources:
        text = src.read_text()
        # HTTPException(status.HTTP_…, "code")와 HTTPException(…, f"prefix_{…}") 형태
        for m in re.finditer(r'HTTPException\(\s*status\.HTTP_\w+,\s*f?"([^"]+)"', text):
            codes.add(m.group(1).split("{")[0])
        # authz.require_role(…, detail="not_reviewer") 형태
        codes.update(re.findall(r'detail="([a-z_]+)"', text))
    assert len(codes) >= 10, f"코드를 거의 못 뽑았다 — 정규식이 소스와 어긋났다: {sorted(codes)}"

    ts = (root / "apps/web/src/shared/api/error_text.ts").read_text()
    exact_block = ts.split("const EXACT", 1)[1].split("\n};", 1)[0]
    exact = set(re.findall(r"^\s+([a-z_0-9]+):\s*\{", exact_block, re.M))
    prefixes = re.findall(r'^\s+\["([^"]+)",', ts.split("const PREFIX", 1)[1], re.M)
    assert exact and prefixes, "사전을 못 읽었다 — error_text.ts 모양이 바뀌었으면 이 시험도 고친다"

    missing = sorted(
        c for c in codes
        if c not in exact and not any(c.startswith(p) for p in prefixes)
    )
    assert not missing, f"화면 사전에 없는 오류 코드: {missing}"


async def test_my_work_history_pages_by_cursor_and_searches_the_whole_set(
    client: AsyncClient, admin_token: str
):
    """내 라벨링 기록을 커서로 끝까지 이어 받고, 검색이 불러온 행 밖까지 찾는지 확인한다.

    최신 N건에서 조용히 끊기면 그 뒤의 건이 내 기록에 없는 것으로 보인다.
    이 테스트는 한 페이지를 2건으로 줄여 같은 상황을 작게 만든다.
    다섯 건이 빠짐없이, 겹침 없이 와야 한다.
    첫 페이지에 없는 가장 오래된 건도 ID 앞자리로 찾을 수 있어야 한다.
    """
    from urllib.parse import quote

    from tests._helpers import add_member, new_project, upload_video

    pid = await new_project(client, admin_token, "wk-hist")
    _, tok = await add_member(client, admin_token, pid, "annotator")
    hdr = {"Authorization": f"Bearer {tok}"}
    vid = await upload_video(client, admin_token, pid)
    made: list[str] = []
    for i in range(5):
        await seed_clip_work(pid, vid, start=i * 5.0, end=i * 5.0 + 5.0)
        wid = (await client.post(f"/projects/{pid}/work/next", headers=hdr)).json()["work_item_id"]
        r = await client.post(f"/work/{wid}/submit", headers=hdr,
                              json={"payload": caption_payload(f"제출 {i}")})
        assert r.status_code == 200, r.text
        made.append(wid)

    seen: list[str] = []
    cursor = ""
    for _ in range(5):
        r = await client.get(f"/projects/{pid}/me/work-history?limit=2{cursor}", headers=hdr)
        assert r.status_code == 200, r.text
        page = r.json()
        if not page:
            break
        seen += [w["work_item_id"] for w in page]
        tail = page[-1]
        # 커서는 서버가 정렬에 쓴 시각(`history_at`)으로 만든다. 화면도 같은 값을 쓴다.
        cursor = f"&before={quote(tail['history_at'])}&before_id={tail['work_item_id']}"
    assert sorted(seen) == sorted(made), "빠지거나 겹친 행이 있다"
    assert seen == list(reversed(made)), "최신 제출이 먼저 와야 한다"

    oldest = made[0]
    r = await client.get(f"/projects/{pid}/me/work-history?limit=2&q={oldest[:8]}", headers=hdr)
    assert [w["work_item_id"] for w in r.json()] == [oldest], r.text
    # 와일드카드는 글자로 취급한다. `_`나 `%`가 모든 행에 맞으면 검색 결과가 틀린다.
    r = await client.get(f"/projects/{pid}/me/work-history?q=%25", headers=hdr)
    assert r.json() == [], r.text


async def _handed_over_setup(client: AsyncClient, admin_token: str) -> dict:
    """A가 제출한 작업이 반려되고 A가 프로젝트를 나간 뒤 B가 가져가 제출하고, A가 다시 초대된 상태를 만든다."""
    from tests._helpers import add_member, new_project, upload_video

    pid = await new_project(client, admin_token, "wk-hand")
    a_uid, a = await add_member(client, admin_token, pid, "annotator")
    b_uid, b = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid, title="넘어간 작업 영상")
    await seed_clip_work(pid, vid, start=12.0, end=40.0)
    ha, hb = {"Authorization": f"Bearer {a}"}, {"Authorization": f"Bearer {b}"}

    wid = (await client.post(f"/projects/{pid}/work/next", headers=ha)).json()["work_item_id"]
    r = await client.post(f"/work/{wid}/submit", headers=ha,
                          json={"payload": caption_payload("A의 제출")})
    assert r.status_code == 200, r.text
    await claim_review(client, rev, pid, wid)
    r = await client.post(f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev}"},
                          json={"decision": "reject", "reason": "다시",
                                "reason_codes": ["caption_inaccurate"]})
    assert r.status_code == 200, r.text
    # 반려로 A에게 돌아온 재작업은 A가 나가면 대기열로 가고, B가 가져가 제출한다.
    r = await client.post(f"/projects/{pid}/me/leave", headers=ha, json={"reason": None})
    assert r.status_code == 200, r.text
    r = await client.post(f"/projects/{pid}/work/next", headers=hb)
    assert r.status_code == 200 and r.json()["work_item_id"] == wid, r.text
    r = await client.post(f"/work/{wid}/submit", headers=hb,
                          json={"payload": caption_payload("B의 제출")})
    assert r.status_code == 200, r.text
    # A가 다시 초대되어 자기 기록을 본다.
    await invite_user(client, admin_token, pid, a_uid, "annotator")
    return {"pid": pid, "wid": wid, "a": ha, "b": hb, "a_uid": a_uid, "b_uid": b_uid}


async def _label_times(wid: str) -> dict[str, str]:
    """작업의 라벨을 사람별 마지막 제출 시각으로 DB에서 직접 읽는다. 화면 API를 거치지 않는다."""
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import Label
    from sqlalchemy import func, select

    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Label.submitted_by, func.max(Label.submitted_at))
            .where(Label.work_item_id == UUID(wid))
            .group_by(Label.submitted_by)
        )).all()
    return {str(uid): at for uid, at in rows}


async def test_my_history_keeps_my_submission_when_the_task_moves_on(
    client: AsyncClient, admin_token: str
):
    """넘어간 작업의 행이 내 제출 시각·작업 시간을 싣고 넘어간 상태로 표시되는지 확인한다.

    작업의 현재 값(`submitted_at`·`claimed_at`·`status`)만 실으면, 작업이 넘어간 뒤 다른 작업자가 제출했을 때 그 사람의 제출 시각과 0초가 내 기록으로 보인다.
    """
    from datetime import datetime

    x = await _handed_over_setup(client, admin_token)
    times = await _label_times(x["wid"])

    r = await client.get(f"/projects/{x['pid']}/me/work-history", headers=x["a"])
    assert r.status_code == 200, r.text
    [row] = r.json()
    assert row["my_state"] == "handed_over", row
    assert datetime.fromisoformat(row["my_submitted_at"]) == times[x["a_uid"]]
    # 작업의 현재 제출 시각은 B가 제출한 시각이다. 이 값이 내 값과 달라야 테스트가 두 값을 구별한다.
    assert datetime.fromisoformat(row["submitted_at"]) > times[x["a_uid"]]
    assert row["my_started_at"] is not None and row["my_work_seconds"] is not None
    assert datetime.fromisoformat(row["my_started_at"]) <= times[x["a_uid"]]
    assert row["video_title"] == "넘어간 작업 영상"
    assert (row["clip_start_seconds"], row["clip_end_seconds"]) == (12.0, 40.0)
    # 다른 사람의 초안·스냅숏은 내 기록에 실리지 않는다.
    assert row["payload"] == {} and row["draft"] is None
    # 판정은 내 마지막 제출에 내린 판정이다. B의 제출은 아직 판정 전이다.
    assert row["my_verdict"] == "reject", row
    assert [x["label"] for x in row["my_reasons"]] == ["캡션 부정확"], row
    assert row["my_submit_count"] == 1, row

    r = await client.get(f"/projects/{x['pid']}/me/work-history", headers=x["b"])
    [row_b] = r.json()
    assert row_b["my_state"] == "submitted", row_b
    assert datetime.fromisoformat(row_b["my_submitted_at"]) == times[x["b_uid"]]
    assert row_b["my_verdict"] is None and row_b["my_reasons"] == [], row_b


async def test_my_history_says_returned_while_nobody_holds_the_task(
    client: AsyncClient, admin_token: str
):
    """반려 뒤 프로젝트를 나가 대기열로 돌아간 작업이 다른 사람이 가져간 상태와 구별되는 `returned`인지 확인한다.

    나간 사람이 다시 초대되면 자기 기록에서 그 작업을 본다.
    """
    from tests._helpers import add_member, new_project, upload_video

    pid = await new_project(client, admin_token, "wk-ret")
    a_uid, a = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    ha = {"Authorization": f"Bearer {a}"}
    wid = (await client.post(f"/projects/{pid}/work/next", headers=ha)).json()["work_item_id"]
    await client.post(f"/work/{wid}/submit", headers=ha, json={"payload": caption_payload("1차")})
    await claim_review(client, rev, pid, wid)
    await client.post(f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev}"},
                      json={"decision": "reject", "reason": "다시"})
    r = await client.post(f"/projects/{pid}/me/leave", headers=ha, json={"reason": None})
    assert r.status_code == 200 and r.json()["released"] == 1, r.text
    await invite_user(client, admin_token, pid, a_uid, "annotator")

    [row] = (await client.get(f"/projects/{pid}/me/work-history", headers=ha)).json()
    assert row["my_state"] == "returned", row
    assert row["my_verdict"] == "reject" and row["my_submitted_at"] is not None, row
    r = await client.get(f"/projects/{pid}/me/work-history?state=returned", headers=ha)
    assert [w["work_item_id"] for w in r.json()] == [wid], r.text


async def test_my_history_reads_a_reviewer_fix_as_the_verdict_on_my_label(
    client: AsyncClient, admin_token: str
):
    """수정 후 승인으로 검수자가 새 라벨을 만들어도 판정이 내 제출의 것이고 작업이 내 기록으로 남는지 확인한다."""
    from tests._helpers import add_member, new_project, upload_video

    pid = await new_project(client, admin_token, "wk-fix")
    a_uid, a = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    ha = {"Authorization": f"Bearer {a}"}
    wid = (await client.post(f"/projects/{pid}/work/next", headers=ha)).json()["work_item_id"]
    await client.post(f"/work/{wid}/submit", headers=ha, json={"payload": caption_payload("내 제출")})
    await claim_review(client, rev, pid, wid)
    r = await client.post(
        f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev}"},
        json={"decision": "modify_and_approve",
              "modified_label_data": caption_payload("검수자가 고친 캡션"),
              "base_label_id": await latest_label_id(client, rev, wid)},
    )
    assert r.status_code == 200, r.text

    [row] = (await client.get(f"/projects/{pid}/me/work-history", headers=ha)).json()
    assert row["my_state"] == "approved", row
    assert row["my_verdict"] == "modify_and_approve" and row["my_submit_count"] == 1, row
    body = (await client.get(f"/projects/{pid}/me/work-history/{wid}", headers=ha)).json()
    assert [lbl["submitted_by"] for lbl in body["labels"]] == [a_uid], body
    assert [v["verdict"] for v in body["reviews"]] == ["modify_and_approve"], body


async def test_my_history_filters_by_state_and_finds_by_title(
    client: AsyncClient, admin_token: str
):
    """상태 거르기와 제목 검색을 서버가 전량에 거는지 확인한다. 실린 행만 거르면 실리지 않은 건이 없는 것으로 보인다."""
    x = await _handed_over_setup(client, admin_token)
    base = f"/projects/{x['pid']}/me/work-history"

    r = await client.get(f"{base}?state=handed_over", headers=x["a"])
    assert [w["work_item_id"] for w in r.json()] == [x["wid"]], r.text
    r = await client.get(f"{base}?state=approved", headers=x["a"])
    assert r.json() == [], r.text
    r = await client.get(f"{base}?q=넘어간", headers=x["a"])
    assert [w["work_item_id"] for w in r.json()] == [x["wid"]], r.text
    r = await client.get(f"{base}?state=nope", headers=x["a"])
    assert r.status_code == 422, r.text


async def test_my_history_rework_is_timed_from_the_rejection(
    client: AsyncClient, admin_token: str
):
    """반려로 돌아온 작업의 작업 시간을 관리자 표의 속도와 같은 구간인 반려 시각부터 재는지 확인한다."""
    from datetime import datetime
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import DomainEvent
    from sqlalchemy import select
    from tests._helpers import add_member, new_project, upload_video

    pid = await new_project(client, admin_token, "wk-rework")
    _, a = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    await seed_clip_work(pid, vid)
    ha = {"Authorization": f"Bearer {a}"}
    wid = (await client.post(f"/projects/{pid}/work/next", headers=ha)).json()["work_item_id"]
    await client.post(f"/work/{wid}/submit", headers=ha, json={"payload": caption_payload("1차")})
    await claim_review(client, rev, pid, wid)
    await client.post(f"/work/{wid}/review", headers={"Authorization": f"Bearer {rev}"},
                      json={"decision": "reject", "reason": "다시"})
    r = await client.post(f"/work/{wid}/submit", headers=ha, json={"payload": caption_payload("2차")})
    assert r.status_code == 200, r.text

    async with SessionLocal() as s:
        rejected_at = await s.scalar(
            select(DomainEvent.occurred_at).where(
                DomainEvent.target_id == UUID(wid), DomainEvent.event_type == "review.reject",
            )
        )
    [row] = (await client.get(f"/projects/{pid}/me/work-history", headers=ha)).json()
    assert row["my_state"] == "submitted", row
    assert datetime.fromisoformat(row["my_started_at"]) == rejected_at


async def test_my_history_detail_opens_my_part_after_the_task_moves_on(
    client: AsyncClient, admin_token: str
):
    """넘어간 작업의 상세에서 내 라벨과 그 검수만 열리고 다른 사람의 라벨은 실리지 않는지 확인한다.

    `/work/{id}/labels`는 지금 보유한 사람과 검수자에게만 열려 있어서, 라벨러가 넘어간 작업의 기록 상세를 여는 데 쓸 수 없다.
    """
    x = await _handed_over_setup(client, admin_token)
    r = await client.get(f"/work/{x['wid']}/labels", headers=x["a"])
    assert r.status_code == 403, "전제: 종전 조회는 넘어간 작업을 라벨러에게 열지 않는다"

    r = await client.get(f"/projects/{x['pid']}/me/work-history/{x['wid']}", headers=x["a"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert {lbl["submitted_by"] for lbl in body["labels"]} == {x["a_uid"]}
    assert [v["verdict"] for v in body["reviews"]] == ["reject"]

    # B의 상세에는 B의 라벨만 있고, A의 라벨을 반려한 기록은 없다.
    r = await client.get(f"/projects/{x['pid']}/me/work-history/{x['wid']}", headers=x["b"])
    body = r.json()
    assert {lbl["submitted_by"] for lbl in body["labels"]} == {x["b_uid"]}
    assert body["reviews"] == []


async def test_my_history_detail_is_404_for_work_that_is_not_mine(
    client: AsyncClient, admin_token: str
):
    """내 기록이 아닌 작업은 존재 여부도 드러내지 않고 404를 주는지 확인한다."""
    from tests._helpers import add_member

    x = await _handed_over_setup(client, admin_token)
    _, c = await add_member(client, admin_token, x["pid"], "annotator")
    r = await client.get(
        f"/projects/{x['pid']}/me/work-history/{x['wid']}",
        headers={"Authorization": f"Bearer {c}"},
    )
    assert r.status_code == 404 and r.json()["detail"] == "work_item_not_found", r.text


async def test_return_and_limit_routes_are_gone(client: AsyncClient, admin_token: str):
    """반납·가져오기·멤버 한도 경로가 없는지 확인한다. 보유는 다음 작업과 제출·판정·신고로만 바뀐다."""
    x = await _claimed(client, admin_token)
    a = {"Authorization": f"Bearer {x['a']}"}
    adm = {"Authorization": f"Bearer {admin_token}"}
    for method, path, body in (
        ("post", f"/work/{x['wid']}/release", None),
        ("post", f"/work/{x['wid']}/review/release", None),
        ("post", f"/projects/{x['pid']}/work/claim", None),
        ("post", f"/projects/{x['pid']}/review/claim", None),
    ):
        r = await getattr(client, method)(path, headers=a, **({"json": body} if body else {}))
        assert r.status_code in (404, 405), f"{path} → {r.status_code}"
    r = await client.patch(f"/projects/{x['pid']}/members/{x['a_uid']}/limit", headers=adm,
                           json={"max_concurrent_assignments": 1})
    assert r.status_code in (404, 405), r.text
    # 보유는 그대로다.
    r = await client.post(f"/projects/{x['pid']}/work/next", headers=a)
    assert (r.json()["work_item_id"], r.json()["resumed"]) == (x["wid"], True)


async def test_deident_report_frees_the_hold_with_an_audit_cause(
    client: AsyncClient, admin_token: str
):
    """비식별화 미흡 신고가 신고한 사람의 보유를 풀고 그 사실을 `work.released`(원인 포함)로 남기는지 확인한다.

    보유가 남으면 신고한 사람의 다음 작업이 멈춘 작업을 계속 다시 연다.
    """
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import DomainEvent, WorkItem
    from sqlalchemy import select

    x = await _claimed(client, admin_token)
    a = {"Authorization": f"Bearer {x['a']}"}
    await client.patch(f"/work/{x['wid']}/draft", headers=a, json={"payload": {"caption": "쓰던 것"}})
    r = await client.post(f"/work/{x['wid']}/report-deident-miss", headers=a,
                          json={"timestamp_ms": 1200, "reason": "얼굴이 보입니다"})
    assert r.status_code == 204, r.text
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(x["wid"]))
        assert (item.status, item.claimed_by, item.draft) == ("available", None, None)
        ev = await s.scalar(select(DomainEvent).where(
            DomainEvent.event_type == "work.released", DomainEvent.target_id == UUID(x["wid"])))
    assert ev is not None and str(ev.actor_user_id) == x["a_uid"]
    assert ev.payload == {"cause": "deident_miss_reported"}
    # 멈춘 작업은 신고한 사람에게도 다른 사람에게도 다시 오지 않는다.
    r = await client.post(f"/projects/{x['pid']}/work/next", headers=a)
    assert r.status_code == 404, r.text
