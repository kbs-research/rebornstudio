"""Vidstack용 presigned GET URL 엔드포인트(GET /videos/{id}/source-url) 테스트."""
import pytest_asyncio
from httpx import AsyncClient
from tests import _storage
from tests._helpers import make_user, put_video_file
from tests.conftest import uniq


@pytest_asyncio.fixture(loop_scope="session")
async def uploaded_video(client: AsyncClient, admin_token: str) -> dict:
    """새 프로젝트에 업로드를 마친 영상 1건과 멤버가 아닌 사용자(outsider)의 토큰을 준비한다."""
    slug = f"src-{uniq('').split('@')[0]}"
    pid = (
        await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"slug": slug, "name": "src test"},
        )
    ).json()["project_id"]
    await _storage.pin_project(pid)
    vid = (
        await client.post(
            f"/projects/{pid}/videos",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": "src-vid", "copyright_policy": "pre_cleared"},
        )
    ).json()["video_id"]
    await put_video_file(client, admin_token, vid, b"x" * 50)

    # outsider 계정을 만든다. 프로젝트 멤버로는 추가하지 않는다.
    _uid, out_tok = await make_user(client, admin_token, "outsider-src")
    return {"project_id": pid, "video_id": vid, "outsider_tok": out_tok}


async def test_source_url_member_can_get(
    client: AsyncClient, admin_token: str, uploaded_video: dict
):
    """멤버(super_admin)는 서명된 URL을 받는다."""
    r = await client.get(
        f"/videos/{uploaded_video['video_id']}/source-url",
        headers={"Authorization": f"Bearer {admin_token}"},  # admin = super_admin
    )
    assert r.status_code == 200
    body = r.json()
    assert body["url"].startswith("http")
    assert "Signature=" in body["url"] or "X-Amz-Signature" in body["url"]
    assert body["expires_in"] > 0


async def test_source_url_outsider_forbidden(
    client: AsyncClient, uploaded_video: dict
):
    """멤버가 아닌 사용자의 요청을 403으로 거절한다."""
    r = await client.get(
        f"/videos/{uploaded_video['video_id']}/source-url",
        headers={"Authorization": f"Bearer {uploaded_video['outsider_tok']}"},
    )
    assert r.status_code == 403


# ── 중복 업로드 감지 ─────────────────────────────────────────

async def test_duplicate_upload_detected_and_marked(client, admin_token):
    """같은 내용을 같은 프로젝트에 두 번 올리면 두 번째 영상에 duplicate_of가 표시된다.

    차단하지 않고 표시만 한다.
    같은 원본을 정당하게 다시 올리는 경우(정책 변경 재등록)가 있어 판단은 사람이 한다.
    멀티파트 ETag(-N)는 파트 해시의 조합이라 비교에 쓰지 않고 완료 요청의 내용 해시로 비교한다.
    """
    from tests._helpers import new_project, upload_video

    pid = await new_project(client, admin_token, "dup")
    vid1 = await upload_video(client, admin_token, pid, title="원본", probed=True)
    vid2 = await upload_video(client, admin_token, pid, title="복사본", probed=True)  # 같은 바이트

    v2 = (await client.get(
        f"/projects/{pid}/videos?bucket=ready&limit=50",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    mine = next(v for v in v2 if v["video_id"] == vid2)
    assert mine["metadata"].get("duplicate_of") == vid1
    assert mine["metadata"].get("duplicate_title") == "원본"
    # 첫 업로드에는 중복 표시가 없다
    first = next(v for v in v2 if v["video_id"] == vid1)
    assert "duplicate_of" not in first["metadata"]


# ── 비식별화 전 원본 노출 방지 ────────────────────────────────────────────────

async def _deident_project_video(client: AsyncClient, admin_token: str) -> tuple[str, str]:
    """비식별화를 켠 프로젝트에 업로드를 마친 영상 1건을 만들고 (project_id, video_id)를 돌려준다.

    재생본이 아직 없어 재생 매체로는 원본만 남은 상태다.
    """
    import uuid

    from api_gateway.database import SessionLocal
    from api_gateway.models import Project
    from tests._helpers import new_project, upload_video

    pid = await new_project(client, admin_token, "src-deid")
    vid = await upload_video(client, admin_token, pid, title="deid-src")
    async with SessionLocal() as s:
        (await s.get(Project, uuid.UUID(pid))).deident_enabled = True
        await s.commit()
    return pid, vid


async def test_source_url_hides_unmasked_original_from_annotator(
    client: AsyncClient, admin_token: str
):
    """비식별화가 끝나지 않은 영상의 원본을 라벨러에게 재생 URL로 주지 않는다.

    재생본이 없을 때 원본으로 대체하면 가리지 않은 얼굴이 라벨러 화면에 나온다.
    관리자와 검수자는 원본 대체를 그대로 받는다.
    """
    from tests._helpers import add_member

    pid, vid = await _deident_project_video(client, admin_token)
    _uid, annot = await add_member(client, admin_token, pid, "annotator")
    _uid, reviewer = await add_member(client, admin_token, pid, "reviewer")

    r = await client.get(f"/videos/{vid}/source-url",
                         headers={"Authorization": f"Bearer {annot}"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "invalid_source_uri"

    for tok in (admin_token, reviewer):
        r = await client.get(f"/videos/{vid}/source-url",
                             headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
        assert r.json()["variant"] == "original"


async def test_source_url_gives_annotator_original_without_deident(
    client: AsyncClient, admin_token: str
):
    """비식별화를 쓰지 않는 영상은 라벨러도 원본 대체를 받는다."""
    from tests._helpers import add_member, new_project, upload_video

    pid = await new_project(client, admin_token, "src-plain")
    vid = await upload_video(client, admin_token, pid, title="plain-src")
    _uid, annot = await add_member(client, admin_token, pid, "annotator")
    r = await client.get(f"/videos/{vid}/source-url",
                         headers={"Authorization": f"Bearer {annot}"})
    assert r.status_code == 200, r.text
    assert r.json()["variant"] == "original"


async def test_source_url_deleted_video_not_found(client: AsyncClient, admin_token: str):
    """삭제한 영상에는 재생 URL을 발급하지 않는다."""
    import uuid
    from datetime import UTC, datetime

    from api_gateway.database import SessionLocal
    from api_gateway.models import Video
    from tests._helpers import new_project, upload_video

    pid = await new_project(client, admin_token, "src-del")
    vid = await upload_video(client, admin_token, pid, title="del-src")
    async with SessionLocal() as s:
        (await s.get(Video, uuid.UUID(vid))).deleted_at = datetime.now(UTC)
        await s.commit()
    r = await client.get(f"/videos/{vid}/source-url",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "video_not_found"
