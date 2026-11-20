"""운영·보안 가드 테스트.

이 파일은 네 가지를 검증한다.

1. 라벨러가 볼 수 있는 범위. 완료 목록·완료 상세·영상 상세의 데이터는 라벨러에게
   열지 않는다. 화면만 막으면 주소로 부르는 경로가 남으므로 서버에서 막는다.
2. 일시정지는 가져오기만 막는다. 새 claim은 409이고, 이미 가진 작업의 제출과 다시 열기는
   그대로 된다. 가진 작업의 제출까지 막으면 그 작업을 끝낼 방법이 없다.
3. 세션 갱신은 기본 조회에서 빠진다. 기록은 남기고, 필터를 켜면 다시 보인다.
4. 감사 보존 기본값은 3년이다. 개인정보 고지와 같은 값이고, 화면은 서버가 준 값을 쓴다.
"""
from uuid import uuid4

from httpx import AsyncClient
from tests._helpers import (
    add_member,
    caption_payload,
    make_account,
    new_project,
    refresh_cookie_of,
    refresh_with_cookie,
    seed_clip_work,
    upload_video,
)


def _h(token: str) -> dict[str, str]:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _pause(client: AsyncClient, admin_token: str, pid: str) -> None:
    """프로젝트를 일시정지한다. 관리자가 설정 화면에서 쓰는 것과 같은 경로다."""
    r = await client.patch(
        f"/projects/{pid}", headers=_h(admin_token), json={"project_status": "paused"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paused"


# 라벨러가 볼 수 있는 범위


async def test_annotator_blocked_from_completed_surfaces(
    client: AsyncClient, admin_token: str
):
    """라벨러가 완료 목록·집계·상세를 주소로 불러도 403인지 확인한다.

    완료 목록에는 다른 사람이 쓰고 승인된 캡션이 모두 있다.
    상세만 막으면 상세에서 막은 내용을 목록이 워크벤치에 보여 준다. 그래서 세 경로를 한 기준으로 막는다.
    """
    pid = await new_project(client, admin_token, "guard-done")
    _, annot = await add_member(client, admin_token, pid, "annotator")

    for path in (
        f"/projects/{pid}/completed",
        f"/projects/{pid}/completed/summary",
        f"/projects/{pid}/completed/{uuid4()}",
    ):
        r = await client.get(path, headers=_h(annot))
        assert r.status_code == 403, f"{path} → {r.status_code} {r.text[:200]}"
        assert r.json()["detail"] == "not_work_result_viewer"


async def test_reviewer_still_sees_completed_surfaces(
    client: AsyncClient, admin_token: str
):
    """검수자가 완료 목록·집계·상세를 계속 볼 수 있는지 확인한다. 막은 대상은 라벨러뿐이다.

    없는 작업 id는 404여야 한다. 404는 권한 검사를 통과해 조회까지 갔다는 뜻이다.
    403이 나오면 권한과 조회 중 어디서 막혔는지 알 수 없어 이 테스트가 권한을 검증하지 못한다.
    """
    pid = await new_project(client, admin_token, "guard-done-ok")
    for role in ("reviewer",):
        _, tok = await add_member(client, admin_token, pid, role)
        r = await client.get(f"/projects/{pid}/completed", headers=_h(tok))
        assert r.status_code == 200, f"{role}: {r.text}"
        r = await client.get(f"/projects/{pid}/completed/summary", headers=_h(tok))
        assert r.status_code == 200, f"{role}: {r.text}"
        r = await client.get(f"/projects/{pid}/completed/{uuid4()}", headers=_h(tok))
        assert r.status_code == 404, f"{role}: {r.text}"


async def test_annotator_blocked_from_video_detail_surfaces(
    client: AsyncClient, admin_token: str
):
    """라벨러가 영상 상세의 준비 타임라인을 열 수 없는지 확인한다.

    준비 타임라인에는 처리 상태와 실패 사유가 실린다.
    라벨러 작업에 필요 없는 정보라서 화면과 같은 기준으로 막는다.
    """
    pid = await new_project(client, admin_token, "guard-vd")
    vid = await upload_video(client, admin_token, pid, title="guarded")
    _, annot = await add_member(client, admin_token, pid, "annotator")

    r = await client.get(f"/videos/{vid}/prep-timeline", headers=_h(annot))
    assert r.status_code == 403, r.text[:200]
    assert r.json()["detail"] == "not_work_result_viewer"


async def test_clip_viewer_stays_open_to_annotator(client: AsyncClient, admin_token: str):
    """클립 뷰어의 데이터가 라벨러에게 계속 열려 있는지 확인한다. 막는 범위가 넓어지지 않았는지 검증한다.

    라벨러도 클립이 어떻게 나뉘었는지 봐야 하고, 그 화면에는 판정도 저장 경로도 없다.
    영상 단건(`GET /videos/{id}`)은 그 화면의 머리글이라 함께 열려 있어야 한다.
    """
    pid = await new_project(client, admin_token, "guard-clips")
    vid = await upload_video(client, admin_token, pid, title="open")
    await seed_clip_work(pid, vid)
    _, annot = await add_member(client, admin_token, pid, "annotator")

    assert (await client.get(f"/videos/{vid}", headers=_h(annot))).status_code == 200
    r = await client.get(f"/projects/{pid}/videos/{vid}/clips", headers=_h(annot))
    assert r.status_code == 200, r.text


# 일시정지는 가져오기만 막는다


async def test_paused_project_blocks_claim_but_allows_submit(
    client: AsyncClient, admin_token: str
):
    """일시정지 뒤 새 배정은 막히고 이미 가진 작업은 제출할 수 있는지 확인한다.

    일시정지는 운영을 잠시 멈추는 기능이다. 가진 작업의 제출까지 막으면 그 작업을 끝낼 방법이 없다.
    """
    pid = await new_project(client, admin_token, "pause-claim")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid, title="p1")
    await seed_clip_work(pid, vid)
    await seed_clip_work(pid, vid, start=10.0, end=15.0)

    held = (await client.post(f"/projects/{pid}/work/next", headers=_h(annot))).json()
    wid = held["work_item_id"]

    await _pause(client, admin_token, pid)

    # 이미 가진 작업은 제출할 수 있다.
    r = await client.post(
        f"/work/{wid}/submit", headers=_h(annot), json={"payload": caption_payload("운영 관문")},
    )
    assert r.status_code == 200, r.text

    # 보유가 없으면 새 배정은 막힌다. 사유는 종료된 프로젝트가 아니라 일시정지다.
    r = await client.post(f"/projects/{pid}/work/next", headers=_h(annot))
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "project_paused"


async def test_paused_project_still_reopens_held_work(client: AsyncClient, admin_token: str):
    """일시정지 중에도 다음 작업이 보유한 작업을 다시 여는지 확인한다. 막으면 보유한 작업을 끝낼 수 없다."""
    pid = await new_project(client, admin_token, "pause-release")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid, title="p2")
    await seed_clip_work(pid, vid)

    wid = (await client.post(
        f"/projects/{pid}/work/next", headers=_h(annot),
    )).json()["work_item_id"]
    await _pause(client, admin_token, pid)

    r = await client.post(f"/projects/{pid}/work/next", headers=_h(annot))
    assert r.status_code == 200, r.text
    assert r.json()["work_item_id"] == wid
    assert r.json()["resumed"] is True


async def test_paused_project_blocks_review_claim(
    client: AsyncClient, admin_token: str
):
    """검수 가져오기도 일시정지에 같은 판정으로 막히는지 확인한다. 라벨링 가져오기만 막으면 다른 경로가 열린다."""
    pid = await new_project(client, admin_token, "pause-rev")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid, title="p3")
    await seed_clip_work(pid, vid)

    wid = (await client.post(
        f"/projects/{pid}/work/next", headers=_h(annot),
    )).json()["work_item_id"]
    await client.post(
        f"/work/{wid}/submit", headers=_h(annot), json={"payload": caption_payload("운영 관문")},
    )
    await _pause(client, admin_token, pid)

    r = await client.post(f"/projects/{pid}/review/next", headers=_h(rev))
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "project_paused"


async def test_active_project_claim_unaffected(client: AsyncClient, admin_token: str):
    """활성 프로젝트의 가져오기가 그대로 되는지 확인한다. 가드가 정상 경로를 막지 않는지 검증한다."""
    pid = await new_project(client, admin_token, "pause-none")
    _, annot = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid, title="p4")
    await seed_clip_work(pid, vid)

    r = await client.post(f"/projects/{pid}/work/next", headers=_h(annot))
    assert r.status_code == 200, r.text


# 감사 보존과 세션 갱신


def test_audit_retention_is_the_notified_three_years():
    """감사 보존 기간이 개인정보 고지(`/privacy`의 "수집일로부터 3년")와 같은 3년인지 확인한다."""
    from api_gateway.services.ops import audit_retention

    assert audit_retention.DEFAULT_RETENTION_DAYS == 1095


async def _refresh_once(client: AsyncClient, email: str, password: str) -> str:
    """로그인한 뒤 refresh를 한 번 해서 `auth.refreshed` 한 행을 만들고 user_id를 돌려준다."""
    login = await client.post("/auth/login", json={"email": email, "password": password})
    sess = login.json()
    r = await refresh_with_cookie(client, refresh_cookie_of(login))
    assert r.status_code == 200, r.text
    return sess["user"]["user_id"]


async def test_session_refresh_out_of_user_activity_and_login_history(
    client: AsyncClient, admin_token: str
):
    """세션 갱신이 사용자 활동 이력과 로그인 이력에서도 보이지 않는지 확인한다.

    활동 이력은 한 사람의 이력이라 세션 갱신을 빼지 않으면 상한 100행이 거의 모두 세션 갱신이 된다.
    로그인 이력은 허용 목록(`auth.login`·`logout`·`login_failed`·`login_locked`)만 보여 준다.
    이 테스트가 그 동작을 고정한다. 허용 목록이 넓어지면 세션 갱신이 섞여 나온다.
    """
    uid, email, pwd, _tok = await make_account(client, admin_token, "refreshme")
    await _refresh_once(client, email, pwd)

    activity = (await client.get(
        f"/admin/users/{uid}/activity?limit=200", headers=_h(admin_token),
    )).json()
    assert not [e for e in activity if e["event_type"] == "auth.refreshed"], activity

    tok = (await client.post(
        "/auth/login", json={"email": email, "password": pwd},
    )).json()["session"]["access_token"]
    history = (await client.get("/me/auth-events?limit=200", headers=_h(tok))).json()
    assert not [e for e in history if e["event_type"] == "auth.refreshed"], history
    assert [e for e in history if e["event_type"] == "auth.login"], history
