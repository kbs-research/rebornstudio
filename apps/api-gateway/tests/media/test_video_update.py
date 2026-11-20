"""영상 메타 편집(PATCH /videos/{id}) 테스트."""
from httpx import AsyncClient
from tests._helpers import add_member, new_project, upload_video


async def test_patch_video_updates_fields(client: AsyncClient, admin_token: str):
    """메타 필드를 갱신하면 PATCH 응답에 새 값이 실린다.

    title, broadcaster, program_name, episode를 확인한다.
    """
    pid = await new_project(client, admin_token, "vid-edit")
    vid = await upload_video(client, admin_token, pid)
    r = await client.patch(
        f"/videos/{vid}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "title": "수정된 제목",
            "broadcaster": "샘플 방송",
            "program_name": "9시 뉴스",
            "episode": "20260528",
            "aired_at": "2026-05-28T21:00:00+09:00",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "수정된 제목"
    assert body["broadcaster"] == "샘플 방송"
    assert body["program_name"] == "9시 뉴스"
    assert body["episode"] == "20260528"


async def test_patch_video_403_for_annotator(client: AsyncClient, admin_token: str):
    """annotator의 영상 메타 편집을 403으로 거절한다."""
    pid = await new_project(client, admin_token, "vid-edit-perm")
    vid = await upload_video(client, admin_token, pid)
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.patch(
        f"/videos/{vid}",
        headers={"Authorization": f"Bearer {annot_tok}"},
        json={"title": "남이 못 바꿈"},
    )
    assert r.status_code == 403


async def test_patch_video_404_for_unknown(client: AsyncClient, admin_token: str):
    """없는 video_id면 404를 돌려준다."""
    missing = "00000000-0000-0000-0000-000000000000"
    r = await client.patch(
        f"/videos/{missing}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"title": "x"},
    )
    assert r.status_code == 404
