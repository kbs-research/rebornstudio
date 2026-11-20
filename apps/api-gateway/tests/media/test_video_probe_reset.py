"""규격 재검사 엔드포인트(POST /projects/{pid}/videos/{vid}/probe/reset) 테스트."""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Video
from httpx import AsyncClient
from tests._helpers import add_member, new_project, upload_video


async def _set_probe_status(video_id: str, probe_status: str) -> None:
    """테스트 준비용으로 영상의 probe_status를 직접 지정한다."""
    async with SessionLocal() as s:
        video = await s.get(Video, UUID(video_id))
        video.probe_status = probe_status
        await s.commit()


async def test_reset_probe_error_to_pending(client: AsyncClient, admin_token: str):
    """error 영상을 재검사 요청하면 pending으로 되돌아간다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "probe-reset")
    vid = await upload_video(client, admin_token, pid)
    await _set_probe_status(vid, "error")

    r = await client.post(f"/projects/{pid}/videos/{vid}/probe/reset", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["probe_status"] == "pending"

    async with SessionLocal() as s:
        video = await s.get(Video, UUID(vid))
    assert video.probe_status == "pending"


async def test_reset_probe_409_when_passed(client: AsyncClient, admin_token: str):
    """passed(정상 통과) 영상의 재검사 요청을 409 not_resettable로 거절한다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "probe-reset-passed")
    vid = await upload_video(client, admin_token, pid)
    await _set_probe_status(vid, "passed")

    r = await client.post(f"/projects/{pid}/videos/{vid}/probe/reset", headers=h)
    assert r.status_code == 409
    assert r.json()["detail"] == "not_resettable"


async def test_reset_probe_403_for_non_admin(client: AsyncClient, admin_token: str):
    """annotator의 재검사 요청을 403으로 거절한다."""
    pid = await new_project(client, admin_token, "probe-reset-perm")
    vid = await upload_video(client, admin_token, pid)
    await _set_probe_status(vid, "error")
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")

    r = await client.post(
        f"/projects/{pid}/videos/{vid}/probe/reset",
        headers={"Authorization": f"Bearer {annot_tok}"},
    )
    assert r.status_code == 403


async def test_reset_probe_404_wrong_project(client: AsyncClient, admin_token: str):
    """다른 프로젝트 ID로 요청하면 404를 돌려준다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "probe-reset-404")
    other_pid = await new_project(client, admin_token, "probe-reset-other")
    vid = await upload_video(client, admin_token, pid)
    await _set_probe_status(vid, "error")

    r = await client.post(f"/projects/{other_pid}/videos/{vid}/probe/reset", headers=h)
    assert r.status_code == 404
