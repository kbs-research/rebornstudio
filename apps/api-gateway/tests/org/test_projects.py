"""프로젝트 CRUD와 멤버 초대 흐름 테스트."""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import ProjectMember
from httpx import AsyncClient
from tests._helpers import invite_user, make_account, make_user
from tests.conftest import uniq


async def test_create_project_then_list(
    client: AsyncClient, admin_token: str
):
    """프로젝트를 만들면 만든 사람이 project_admin이 되고 단건 조회가 되는지 확인한다."""
    slug = f"pytest-{uniq('').split('@')[0]}"
    r = await client.post(
        "/projects",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"slug": slug, "name": "pytest project"},
    )
    assert r.status_code == 201
    pid = r.json()["project_id"]
    assert r.json()["my_role"] == "project_admin"

    r = await client.get(
        f"/projects/{pid}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    assert r.json()["slug"] == slug


async def test_create_project_duplicate_slug(
    client: AsyncClient, admin_token: str
):
    """같은 slug로 다시 만들면 409인지 확인한다."""
    slug = f"dup-{uniq('').split('@')[0]}"
    body = {"slug": slug, "name": "x"}
    r1 = await client.post(
        "/projects", headers={"Authorization": f"Bearer {admin_token}"}, json=body
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/projects", headers={"Authorization": f"Bearer {admin_token}"}, json=body
    )
    assert r2.status_code == 409


async def test_invited_member_sees_project(
    client: AsyncClient, admin_token: str
):
    """관리자가 초대하면 그 사용자의 프로젝트 목록에 나타나는지 확인한다. 멤버는 초대로만 들어온다."""
    user_id, user_tok = await make_user(client, admin_token, "member")
    pid = (
        await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "slug": f"jt-{uniq('').split('@')[0]}",
                "name": "joinable",
            },
        )
    ).json()["project_id"]

    await invite_user(client, admin_token, pid, user_id, "annotator")

    r = await client.get(
        "/projects", headers={"Authorization": f"Bearer {user_tok}"}
    )
    assert any(p["project_id"] == pid for p in r.json())


async def test_project_stats(client: AsyncClient, admin_token: str):
    """프로젝트 통계의 상태별 합계가 전체 수와 같은지 확인한다."""
    r = await client.get(
        "/projects", headers={"Authorization": f"Bearer {admin_token}"}
    )
    pid = r.json()[0]["project_id"]
    r = await client.get(
        f"/projects/{pid}/stats", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    body = r.json()
    # 합계가 일치한다
    assert sum(body["work_by_status"].values()) == body["total_work_items"]
    assert body["total_videos"] >= 0


# project_admin이 이메일로 기존 사용자를 초대한다


async def test_invite_member_ok(client: AsyncClient, admin_token: str):
    """승인된 사용자를 이메일로 초대하면 바로 active 멤버가 되는지 확인한다."""
    from tests._helpers import make_user, new_project
    pid = await new_project(client, admin_token, "inv")
    uid, _ = await make_user(client, admin_token, "invitee")
    # 초대할 사용자의 이메일을 조회한다
    email_r = await client.get("/users", headers={"Authorization": f"Bearer {admin_token}"})
    email = next(u["email"] for u in email_r.json() if u["user_id"] == uid)
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        f"/projects/{pid}/members/invite",
        json={"email": email, "role": "annotator"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    assert r.json()["role"] == "annotator"
    assert r.json()["user_id"] == uid


async def test_invite_member_unknown_email_404(client: AsyncClient, admin_token: str):
    """계정이 없는 이메일로 초대하면 404 `invitee_unavailable`인지 확인한다."""
    from tests._helpers import new_project
    from tests.conftest import uniq
    pid = await new_project(client, admin_token, "inv404")
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        f"/projects/{pid}/members/invite",
        json={"email": uniq("nobody"), "role": "annotator"},
        headers=h,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "invitee_unavailable"


async def test_invite_member_duplicate_409(client: AsyncClient, admin_token: str):
    """이미 멤버인 사용자를 다시 초대하면 409인지 확인한다."""
    from tests._helpers import make_user, new_project
    pid = await new_project(client, admin_token, "inv409")
    uid, _ = await make_user(client, admin_token, "invdup")
    email_r = await client.get("/users", headers={"Authorization": f"Bearer {admin_token}"})
    email = next(u["email"] for u in email_r.json() if u["user_id"] == uid)
    h = {"Authorization": f"Bearer {admin_token}"}
    # 첫 번째 초대
    r = await client.post(
        f"/projects/{pid}/members/invite",
        json={"email": email, "role": "annotator"},
        headers=h,
    )
    assert r.status_code == 200
    # 두 번째 초대는 409다
    r = await client.post(
        f"/projects/{pid}/members/invite",
        json={"email": email, "role": "reviewer"},
        headers=h,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "already_member"


async def test_list_members_limit(client: AsyncClient, admin_token: str):
    """`?limit=1`이면 멤버가 2명 이상인 프로젝트에서 1건만 돌려주는지 확인한다."""
    from tests._helpers import add_member, new_project
    pid = await new_project(client, admin_token, "lim")
    # 관리자 1명에 annotator 2명을 더한다
    await add_member(client, admin_token, pid, "annotator")
    await add_member(client, admin_token, pid, "annotator")
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.get(f"/projects/{pid}/members?limit=1", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == 1


async def test_list_members_default_returns_all(client: AsyncClient, admin_token: str):
    """limit 없이 조회하면 멤버 전체(3명 이상)를 돌려주는지 확인한다."""
    from tests._helpers import add_member, new_project
    pid = await new_project(client, admin_token, "limall")
    await add_member(client, admin_token, pid, "annotator")
    await add_member(client, admin_token, pid, "annotator")
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.get(f"/projects/{pid}/members", headers=h)
    assert r.status_code == 200
    assert len(r.json()) >= 3


# 멤버십의 실패 경로


async def _account(client: AsyncClient, admin_token: str, prefix: str) -> tuple[str, str, str]:
    """활성 계정을 만들고 (uid, token, email)을 돌려준다.

    초대 테스트는 이메일이 필요해서 `make_user` 대신 `make_account`를 쓴다.
    """
    uid, email, _pwd, tok = await make_account(client, admin_token, prefix)
    return uid, tok, email


async def _member_row(client: AsyncClient, admin_token: str, pid: str, uid: str) -> dict | None:
    """멤버 목록에서 한 사람의 행을 찾는다. 없으면 None이다."""
    rows = (await client.get(
        f"/projects/{pid}/members", headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    return next((m for m in rows if m["user_id"] == uid), None)


async def test_invite_brings_back_member_who_left(client: AsyncClient, admin_token: str):
    """나간 사람을 초대하면 기존 멤버 행을 다시 활성화하는지 확인한다.

    되살린 행은 새 초대와 같은 형태다. 이번 역할을 쓴다.
    나가기 화면이 "다시 배정받으려면 관리자의 초대가 필요합니다"라고 안내하므로 그 초대가 막히면 안 된다.
    """
    from tests._helpers import new_project
    pid = await new_project(client, admin_token, "reinv")
    ah = {"Authorization": f"Bearer {admin_token}"}
    uid, tok, email = await _account(client, admin_token, "reinv")
    assert (await client.post(f"/projects/{pid}/members/invite", headers=ah,
                              json={"email": email, "role": "annotator"})).status_code == 200
    # 화면의 `프로젝트 나가기`와 같은 경로로 나간다.
    r = await client.post(f"/projects/{pid}/me/leave",
                          headers={"Authorization": f"Bearer {tok}"}, json={"reason": "계약 종료"})
    assert r.status_code == 200, r.text
    assert (await _member_row(client, admin_token, pid, uid))["status"] == "left"

    r = await client.post(f"/projects/{pid}/members/invite", headers=ah,
                          json={"email": email, "role": "reviewer"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active" and body["role"] == "reviewer"
    assert "max_concurrent_assignments" not in body

    # 이전 상태와 역할은 감사 기록에 남는다. 되돌릴 근거가 된다.
    from tests._helpers import events
    ev = await events(project_id=pid, event_type="member.invited")
    latest = ev[0]["payload"]
    assert latest["reinvited"] is True
    assert latest["previous_status"] == "left"
    assert latest["previous_role"] == "annotator"


async def _seed_membership(pid: str, uid: str, status: str) -> None:
    """초대를 거치지 않은 멤버십 행을 직접 넣는다. 이전 버전의 참여 요청 기능이 남긴 데이터를 흉내 낸다."""
    async with SessionLocal() as s:
        s.add(ProjectMember(project_id=UUID(pid), user_id=UUID(uid), role="annotator", status=status))
        await s.commit()


async def test_invite_brings_back_rejected_or_pending_rows(client: AsyncClient, admin_token: str):
    """거절됐거나 승인을 기다리던 행의 사용자도 초대하면 활성 멤버가 되는지 확인한다.

    참여 요청과 승인 경로가 없으므로 남은 행을 되살릴 길은 초대뿐이다.
    """
    from tests._helpers import new_project
    pid = await new_project(client, admin_token, "reinvrj")
    ah = {"Authorization": f"Bearer {admin_token}"}
    for status in ("rejected", "pending"):
        uid, _tok, email = await _account(client, admin_token, f"reinv{status[:2]}")
        await _seed_membership(pid, uid, status)
        r = await client.post(f"/projects/{pid}/members/invite", headers=ah,
                              json={"email": email, "role": "annotator"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"


async def test_invite_refuses_accounts_that_cannot_log_in(client: AsyncClient, admin_token: str):
    """정지 계정을 초대하지 않는지 확인한다.

    로그인이 막힌 계정(`account_suspended`)을 들이면 목록에는 `활성`인데 일할 수 없는 사람이 생긴다.
    그래서 막고 멤버 행을 만들지 않는다. 응답은 없는 계정과 같은 404 `invitee_unavailable`이다.
    """
    from tests._helpers import new_project
    pid = await new_project(client, admin_token, "invinact")
    ah = {"Authorization": f"Bearer {admin_token}"}

    susp_uid, _, susp_email = await _account(client, admin_token, "invsusp")
    r = await client.patch(f"/admin/users/{susp_uid}/status", headers=ah,
                           json={"status": "suspended"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/projects/{pid}/members/invite", headers=ah,
                          json={"email": susp_email, "role": "annotator"})
    assert r.status_code == 404
    assert r.json()["detail"] == "invitee_unavailable"
    assert await _member_row(client, admin_token, pid, susp_uid) is None


async def test_remove_member_failure_paths(client: AsyncClient, admin_token: str):
    """멤버 제거의 실패 세 가지(검수자 403, 없는 멤버 404, 마지막 관리자 409)를 확인한다.

    검수자는 멤버 화면을 볼 수 있지만 제거는 관리자만 할 수 있다(`require_project_admin`).
    """
    from uuid import uuid4

    from tests._helpers import add_member, new_project
    pid = await new_project(client, admin_token, "rmfail")
    ah = {"Authorization": f"Bearer {admin_token}"}
    worker_uid, _ = await add_member(client, admin_token, pid, "annotator")
    _op_uid, op_tok = await add_member(client, admin_token, pid, "reviewer")

    r = await client.delete(f"/projects/{pid}/members/{worker_uid}",
                            headers={"Authorization": f"Bearer {op_tok}"})
    assert r.status_code == 403
    assert r.json()["detail"] == "not_project_admin"

    r = await client.delete(f"/projects/{pid}/members/{uuid4()}", headers=ah)
    assert r.status_code == 404
    assert r.json()["detail"] == "membership_not_found"

    # 프로젝트를 만든 사람이 유일한 관리자다. 스스로를 빼면 프로젝트를 운영할 사람이 없다.
    me = (await client.get("/auth/me", headers=ah)).json()["user_id"]
    r = await client.delete(f"/projects/{pid}/members/{me}", headers=ah)
    assert r.status_code == 409
    assert r.json()["detail"] == "last_admin_cannot_leave"


async def test_left_member_row_can_be_removed_and_is_audited(
    client: AsyncClient, admin_token: str
):
    """나간 사람의 행도 목록에서 지울 수 있고, 지운 일이 감사 기록에 남는지 확인한다.

    행이 사라지므로 감사 기록이 유일한 흔적이다. 어떤 상태에서 누가 뺐는지까지 남긴다.
    """
    from tests._helpers import add_member, new_project
    pid = await new_project(client, admin_token, "rmleft")
    ah = {"Authorization": f"Bearer {admin_token}"}
    uid, tok = await add_member(client, admin_token, pid, "annotator")
    await client.post(f"/projects/{pid}/me/leave",
                      headers={"Authorization": f"Bearer {tok}"}, json={})

    r = await client.delete(f"/projects/{pid}/members/{uid}", headers=ah)
    assert r.status_code == 204, r.text
    assert await _member_row(client, admin_token, pid, uid) is None

    from tests._helpers import events
    ev = await events(project_id=pid, event_type="member.removed")
    assert ev and ev[0]["target_id"] == uid
    assert ev[0]["payload"]["status"] == "left"


async def test_role_change_guards_last_admin_and_is_audited(
    client: AsyncClient, admin_token: str
):
    """마지막 관리자의 역할 변경은 409이고, 바꾼 역할은 감사 기록에 남는지 확인한다.

    역할 변경은 권한을 바꾸는 조작이라 감사 기록에 남아야 한다.
    """
    from tests._helpers import add_member, new_project
    pid = await new_project(client, admin_token, "rolegd")
    ah = {"Authorization": f"Bearer {admin_token}"}
    me = (await client.get("/auth/me", headers=ah)).json()["user_id"]

    r = await client.patch(f"/projects/{pid}/members/{me}/role", headers=ah,
                           json={"role": "annotator"})
    assert r.status_code == 409
    assert r.json()["detail"] == "last_admin_cannot_leave"

    uid, _ = await add_member(client, admin_token, pid, "annotator")
    r = await client.patch(f"/projects/{pid}/members/{uid}/role", headers=ah,
                           json={"role": "reviewer"})
    assert r.status_code == 200 and r.json()["role"] == "reviewer"
    from tests._helpers import events
    ev = await events(project_id=pid, event_type="member.role_changed")
    assert ev and ev[0]["payload"] == {"before": "annotator", "after": "reviewer"}


async def test_project_admin_without_platform_role_cannot_delete_project(
    client: AsyncClient, admin_token: str
):
    """프로젝트 관리자(일반 계정)의 프로젝트 삭제가 403인지 확인한다.

    프로젝트 삭제는 최고관리자만 할 수 있다.
    서버가 이 규칙으로 막으므로 화면은 이 사람에게 삭제 버튼을 그리지 않는다.
    """
    from tests._helpers import add_member, new_project
    pid = await new_project(client, admin_token, "delgd")
    _uid, tok = await add_member(client, admin_token, pid, "project_admin")
    r = await client.delete(f"/projects/{pid}", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
    assert r.json()["detail"] == "not_super_admin"


async def test_member_list_has_no_silent_cap(client: AsyncClient, admin_token: str):
    """멤버 목록이 200명에서 조용히 잘리지 않는지 확인한다.

    201명을 API로 만들면 느리므로 DB에 직접 넣는다. 목록 조회만 확인하는 테스트다.
    이메일은 `uniq()` 형식으로 둔다. 세션 끝 정리(`conftest`)가 그 패턴으로 지운다.
    화면이 모르는 상한이 있으면 그 뒤의 멤버가 멤버 표·검색·문의 작성자 조회에서 빠진다.
    """
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import ProjectMember, User
    from tests._helpers import new_project
    pid = await new_project(client, admin_token, "cap")
    async with SessionLocal() as s:
        for i in range(205):
            u = User(email=uniq(f"cap{i}"), display_name=f"cap{i}", approval_status="active")
            s.add(u)
            await s.flush()
            s.add(ProjectMember(project_id=UUID(pid), user_id=u.user_id, role="annotator",
                                status="active"))
        await s.commit()
    rows = (await client.get(f"/projects/{pid}/members",
                             headers={"Authorization": f"Bearer {admin_token}"})).json()
    assert len(rows) == 206  # 만든 관리자 1명과 205명


async def test_project_slug_rule_matches_screen(client: AsyncClient, admin_token: str):
    """주소 식별자가 3~50자이고 첫 글자가 영문 소문자나 숫자인지 확인한다.

    화면(`project_new.tsx`)도 같은 식을 쓴다. 한쪽에서만 통과하는 입력이 없어야 한다.
    """
    h = {"Authorization": f"Bearer {admin_token}"}
    for bad in ("ab", "-abc", "Abc", "a_b"):
        r = await client.post("/projects", headers=h,
                              json={"slug": bad, "name": "slug rule"})
        assert r.status_code == 422, (bad, r.status_code)
