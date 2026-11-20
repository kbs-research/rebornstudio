"""Video 도메인 테스트. 등록 초기 상태, 영상 권리 상태 제약, 권한, 422, soft delete를 확인한다."""
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import User, Video
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from tests._helpers import add_member, make_user, new_project, upload_video


async def test_registration_never_gates_on_policy(
    client: AsyncClient, admin_token: str
):
    """등록 영상은 정책과 관계없이 `cleared`로 시작한다. 게이트는 '분석 요청'이다.

    비용과 외부 전송은 요청 클릭에서 생긴다.
    정책 필드는 남아 있지만(이력·통계·우선순위가 읽는다) 초기 상태를 정하지 않는다.
    등록 시 정책은 파일명만 보고 고르는 값이라 게이트로 쓰지 않는다.
    """
    pid = await new_project(client, admin_token, "policy")
    r = await client.post(
        f"/projects/{pid}/videos",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"title": "pre", "copyright_policy": "pre_cleared"},
    )
    assert r.status_code == 201
    assert r.json()["copyright_policy"] == "pre_cleared"
    # 지원하지 않는 정책은 계약이 거절한다.
    r = await client.post(
        f"/projects/{pid}/videos",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"title": "rev", "copyright_policy": "review_required"},
    )
    assert r.status_code == 422

    # 정책을 아예 보내지 않아도 등록된다. 화면에는 이 선택이 없다.
    r = await client.post(
        f"/projects/{pid}/videos",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"title": "no-policy"},
    )
    assert r.status_code == 201
    assert r.json()["copyright_policy"] == "pre_cleared"


async def test_video_without_status_gets_the_registration_answer(
    client: AsyncClient, admin_token: str
):
    """상태를 넘기지 않고 만든 영상도 `cleared`로 시작하는지 ORM과 DB 기본값 둘 다 확인한다.

    두 층을 따로 확인한다. ORM 기본값만 고치면 원시 SQL이, DB 기본값만 고치면 ORM이 다른 값을 돌려준다.
    """
    pid = UUID(await new_project(client, admin_token, "cpdefault"))
    async with SessionLocal() as s:
        admin_id = await s.scalar(
            select(User.user_id).where(User.email == "admin@example.com"))
        orm = Video(project_id=pid, uploaded_by=admin_id, title="orm",
                    copyright_policy="pre_cleared")
        s.add(orm)
        await s.flush()
        orm_status = await s.scalar(
            select(Video.copyright_status).where(Video.video_id == orm.video_id))
        raw_status = await s.scalar(text(
            "INSERT INTO videos (project_id, uploaded_by, title, copyright_policy) "
            "VALUES (:p, :u, 'raw', 'pre_cleared') RETURNING copyright_status"
        ), {"p": pid, "u": admin_id})
        await s.commit()
    # 두 값을 한 번에 비교한다. 실패 메시지가 다른 값을 돌려준 층을 함께 표시한다.
    # (ORM, DB) 순서다. DB 쪽만 `pending`이면 마이그레이션 0084가 적용되지 않은 DB다.
    assert (orm_status, raw_status) == ("cleared", "cleared")


async def test_copyright_status_accepts_only_cleared(client: AsyncClient, admin_token: str):
    """영상 권리 상태 제약이 `cleared` 외의 값을 거절하는지 확인한다.

    `pending`·`rejected` 영상을 처리할 경로가 없다.
    제약이 그 값을 받으면 어느 화면에도 잡히지 않고 분석 대기에서도 빠진 영상이 생긴다.
    """
    pid = UUID(await new_project(client, admin_token, "cponly"))
    async with SessionLocal() as s:
        admin_id = await s.scalar(
            select(User.user_id).where(User.email == "admin@example.com"))
    for bad in ("pending", "rejected"):
        async with SessionLocal() as s:
            with pytest.raises(IntegrityError, match="ck_video_copyright_status"):
                await s.execute(text(
                    "INSERT INTO videos (project_id, uploaded_by, title, copyright_policy,"
                    " copyright_status) VALUES (:p, :u, 'bad', 'pre_cleared', :st)"
                ), {"p": pid, "u": admin_id, "st": bad})
            await s.rollback()


async def test_invalid_policy_422(client: AsyncClient, admin_token: str):
    """이전 정책 값(`public`)으로 등록하면 422로 거절한다."""
    pid = await new_project(client, admin_token, "bad")
    r = await client.post(
        f"/projects/{pid}/videos",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"title": "x", "copyright_policy": "public"},  # 이전 enum 값
    )
    assert r.status_code == 422


async def test_list_videos_requires_member(client: AsyncClient, admin_token: str):
    """멤버가 아닌 사용자의 영상 목록 조회를 403으로 거절한다."""
    pid = await new_project(client, admin_token, "perm")
    await client.post(
        f"/projects/{pid}/videos",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"title": "m", "copyright_policy": "pre_cleared"},
    )
    _, outsider_tok = await make_user(client, admin_token, "outsider")
    r = await client.get(
        f"/projects/{pid}/videos", headers={"Authorization": f"Bearer {outsider_tok}"}
    )
    assert r.status_code == 403


async def test_create_video_annotator_forbidden(
    client: AsyncClient, admin_token: str
):
    """영상 등록은 project_admin만 할 수 있다. annotator의 등록 요청은 403으로 거절한다."""
    pid = await new_project(client, admin_token, "annot")
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.post(
        f"/projects/{pid}/videos",
        headers={"Authorization": f"Bearer {annot_tok}"},
        json={"title": "x", "copyright_policy": "pre_cleared"},
    )
    assert r.status_code == 403


async def test_video_ignores_copyright_holder(
    client: AsyncClient, admin_token: str
):
    """등록 요청의 copyright_holder는 무시하고 조회 응답에도 싣지 않는다."""
    pid = await new_project(client, admin_token, "clear")
    vid = (
        await client.post(
            f"/projects/{pid}/videos",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "title": "clear",
                "copyright_policy": "pre_cleared",
                "copyright_holder": "샘플 방송",
            },
        )
    ).json()["video_id"]
    r = await client.get(
        f"/videos/{vid}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    assert "copyright_holder" not in r.json()


# ── 영상·프로젝트 soft delete ──────────────────────────────────


async def test_video_soft_delete(client: AsyncClient, admin_token: str):
    """DELETE /videos/{id}는 204이고, 이후 GET은 404이며 목록에서 숨긴다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "vdel")
    vid = await upload_video(client, admin_token, pid)
    assert (await client.delete(f"/videos/{vid}", headers=h)).status_code == 204
    assert (await client.get(f"/videos/{vid}", headers=h)).status_code == 404
    vids = (await client.get(f"/projects/{pid}/videos", headers=h)).json()
    assert not any(v["video_id"] == vid for v in vids)


async def test_video_soft_delete_requires_admin(client: AsyncClient, admin_token: str):
    """annotator의 영상 삭제 요청을 403으로 거절한다."""
    pid = await new_project(client, admin_token, "vdelp")
    vid = await upload_video(client, admin_token, pid)
    _, atok = await add_member(client, admin_token, pid, "annotator")
    assert (await client.delete(f"/videos/{vid}", headers={"Authorization": f"Bearer {atok}"})).status_code == 403


async def test_project_soft_delete(client: AsyncClient, admin_token: str):
    """DELETE /projects/{id}는 204이고, 이후 목록에서 숨긴다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "pdel")
    assert (await client.delete(f"/projects/{pid}", headers=h)).status_code == 204
    projects = (await client.get("/projects", headers=h)).json()
    assert not any(p["project_id"] == pid for p in projects)


async def test_project_soft_delete_requires_super(client: AsyncClient, admin_token: str):
    """annotator의 프로젝트 삭제 요청을 403으로 거절한다."""
    pid = await new_project(client, admin_token, "pdelp")
    _, atok = await add_member(client, admin_token, pid, "annotator")
    assert (await client.delete(f"/projects/{pid}", headers={"Authorization": f"Bearer {atok}"})).status_code == 403
