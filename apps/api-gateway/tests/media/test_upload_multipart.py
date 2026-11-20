"""멀티파트 업로드 테스트. 3GB가 90%에서 끊겨도 끊긴 파트부터 이어 올린다.

presigned POST 하나로 파일 전체를 올리면 실패할 때 처음부터 다시 올려야 한다.
세 가지를 확인한다.

  1. 다시 요청하면 같은 upload_id와 이미 올라간 파트를 돌려준다(이어받기).
  2. 완료할 때 파트 목록은 서버가 만든다. 클라이언트가 ETag를 보관하지 않는다.
  3. 멀티파트 ETag는 파트 해시의 조합이라 내용 해시로 쓸 수 없다. 그래서 중복 감지는 클라이언트 내용 해시로 한다.
"""
import httpx
import pytest
from api_gateway.services.media import storage
from httpx import AsyncClient
from tests._helpers import new_project

_PART = 16 * 1024 * 1024


async def _new_video(client: AsyncClient, token: str, pid: str, title: str) -> str:
    """제목만으로 영상 행을 만들고 video_id를 돌려준다."""
    r = await client.post(f"/projects/{pid}/videos", headers={"Authorization": f"Bearer {token}"},
                          json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()["video_id"]


async def _init(client: AsyncClient, token: str, vid: str) -> dict:
    """멀티파트 업로드를 시작하거나 이어받을 정보를 받는다."""
    r = await client.post(f"/videos/{vid}/upload/multipart",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()


async def _put_part(client: AsyncClient, token: str, vid: str, upload_id: str,
                    n: int, body: bytes) -> None:
    """파트 하나의 presigned URL을 받아 실제로 PUT한다."""
    r = await client.post(f"/videos/{vid}/upload/parts",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"upload_id": upload_id, "part_numbers": [n]})
    assert r.status_code == 200, r.text
    url = r.json()["urls"][str(n)]
    # presigned PUT은 moto가 서명까지 검증한다. 실제 업로드 경로와 같다.
    resp = httpx.put(url, content=body)
    assert resp.status_code == 200, resp.text


async def test_resume_returns_same_upload_and_uploaded_parts(
    client: AsyncClient, admin_token: str
):
    """탭을 닫았다가 돌아와도 이어 올린다. 같은 id와 이미 올라간 파트 번호를 돌려준다."""
    pid = await new_project(client, admin_token, "mpu-resume")
    vid = await _new_video(client, admin_token, pid, "resume")

    first = await _init(client, admin_token, vid)
    assert first["part_size"] == _PART
    assert first["uploaded_parts"] == []

    await _put_part(client, admin_token, vid, first["upload_id"], 1, b"a" * _PART)

    again = await _init(client, admin_token, vid)
    assert again["upload_id"] == first["upload_id"], "새로 시작하면 올린 파트를 버린다"
    assert again["uploaded_parts"] == [1]


async def test_complete_assembles_parts_server_side(client: AsyncClient, admin_token: str):
    """완료할 때 파트 목록은 서버가 ListParts로 만든다. 클라이언트가 보관하는 상태를 줄인다."""
    pid = await new_project(client, admin_token, "mpu-complete")
    vid = await _new_video(client, admin_token, pid, "complete")
    init = await _init(client, admin_token, vid)

    await _put_part(client, admin_token, vid, init["upload_id"], 1, b"x" * _PART)
    await _put_part(client, admin_token, vid, init["upload_id"], 2, b"y" * 1024)

    r = await client.post(f"/videos/{vid}/upload/complete",
                          headers={"Authorization": f"Bearer {admin_token}"},
                          json={"upload_id": init["upload_id"],
                                "expected_size_bytes": _PART + 1024})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["upload_status"] == "uploaded"
    assert body["file_size_bytes"] == _PART + 1024
    # 이어받기 상태는 지운다. 남으면 재업로드 때 이미 끝난 업로드 id로 이어 올리려 한다.
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import Video
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        assert "mpu_upload_id" not in (v.extra or {})


async def test_duplicate_detected_by_content_hash(client: AsyncClient, admin_token: str):
    """중복 업로드를 클라이언트 내용 해시로 감지해 표시한다. 멀티파트 ETag는 파트 해시의 조합이라 비교에 쓰지 않는다.

    중복은 차단하지 않고 표시만 한다.
    같은 원본을 정책을 달리해 다시 올리는 경우가 있어 판단은 사람이 한다.
    """
    pid = await new_project(client, admin_token, "mpu-dup")
    hashed = "deadbeef" * 8 + "-1"

    ids = []
    for title in ("첫 번째", "두 번째"):
        vid = await _new_video(client, admin_token, pid, title)
        init = await _init(client, admin_token, vid)
        await _put_part(client, admin_token, vid, init["upload_id"], 1, b"z" * 2048)
        r = await client.post(f"/videos/{vid}/upload/complete",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"upload_id": init["upload_id"], "content_hash": hashed})
        assert r.status_code == 200, r.text
        ids.append(vid)

    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import Video
    async with SessionLocal() as s:
        second = await s.get(Video, UUID(ids[1]))
        assert second.extra.get("duplicate_of") == ids[0]
        assert second.extra.get("duplicate_title") == "첫 번째"
        first = await s.get(Video, UUID(ids[0]))
        assert "duplicate_of" not in (first.extra or {}), "첫 건은 중복이 아니다"


async def test_complete_without_any_part_is_rejected(client: AsyncClient, admin_token: str):
    """파트가 하나도 올라오지 않은 완료 요청을 400으로 거절한다. 빈 객체를 만들고 성공으로 처리하지 않는다."""
    pid = await new_project(client, admin_token, "mpu-empty")
    vid = await _new_video(client, admin_token, pid, "empty")
    init = await _init(client, admin_token, vid)

    r = await client.post(f"/videos/{vid}/upload/complete",
                          headers={"Authorization": f"Bearer {admin_token}"},
                          json={"upload_id": init["upload_id"]})
    assert r.status_code == 400
    assert r.json()["detail"] == "object_not_uploaded"


async def test_size_mismatch_is_rejected(client: AsyncClient, admin_token: str):
    """올라간 크기가 보낸 크기와 다르면 400 `size_mismatch`로 거절한다.

    화면은 이 코드를 문구 사전으로 바꿔 "업로드를 다시 시도해주세요"를 표시한다.
    """
    pid = await new_project(client, admin_token, "mpu-size")
    vid = await _new_video(client, admin_token, pid, "size")
    init = await _init(client, admin_token, vid)
    await _put_part(client, admin_token, vid, init["upload_id"], 1, b"s" * 2048)

    r = await client.post(f"/videos/{vid}/upload/complete",
                          headers={"Authorization": f"Bearer {admin_token}"},
                          json={"upload_id": init["upload_id"], "expected_size_bytes": 4096})
    assert r.status_code == 400
    assert r.json()["detail"] == "size_mismatch"


async def test_playback_before_upload_is_a_conflict(client: AsyncClient, admin_token: str):
    """업로드 전 재생 주소 요청을 409 `video_not_uploaded`로 거절한다. 없는 객체를 서명하지 않는다."""
    pid = await new_project(client, admin_token, "mpu-play")
    vid = await _new_video(client, admin_token, pid, "play")
    r = await client.get(f"/videos/{vid}/source-url",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 409
    assert r.json()["detail"] == "video_not_uploaded"


async def test_deleting_an_unfinished_upload_aborts_it(client: AsyncClient, admin_token: str):
    """업로드가 끝나지 않은 영상을 지우면 미완료 멀티파트도 중단한다.

    등록 시트의 `큐에서 제거`와 `중단`이 이 경로를 쓴다.
    중단하지 않으면 올라간 파트가 계속 과금되고 콘솔에도 보이지 않는다.
    """
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import Video

    pid = await new_project(client, admin_token, "mpu-abort")
    vid = await _new_video(client, admin_token, pid, "abort")
    init = await _init(client, admin_token, vid)
    await _put_part(client, admin_token, vid, init["upload_id"], 1, b"q" * 1024)

    r = await client.delete(f"/videos/{vid}", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code in (200, 204), r.text
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        assert v.deleted_at is not None
        assert "mpu_upload_id" not in (v.extra or {}), "죽은 업로드 id가 남았다"
    # 스토리지에서도 지워졌다. 없는 업로드의 파트 목록을 읽으면 오류가 난다.
    with pytest.raises(Exception):  # noqa: B017 — moto가 NoSuchUpload를 낸다
        storage.list_parts(init["s3_key"], init["upload_id"])


@pytest.mark.parametrize("path", ["multipart", "parts"])
async def test_uploaded_video_rejects_further_upload(
    client: AsyncClient, admin_token: str, path: str
):
    """이미 올라간 영상의 추가 업로드 요청을 409로 거절한다."""
    pid = await new_project(client, admin_token, f"mpu-done-{path}")
    vid = await _new_video(client, admin_token, pid, "done")
    init = await _init(client, admin_token, vid)
    await _put_part(client, admin_token, vid, init["upload_id"], 1, b"q" * 2048)
    await client.post(f"/videos/{vid}/upload/complete",
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"upload_id": init["upload_id"]})

    body = {"upload_id": init["upload_id"], "part_numbers": [2]} if path == "parts" else None
    r = await client.post(f"/videos/{vid}/upload/{path}",
                          headers={"Authorization": f"Bearer {admin_token}"}, json=body)
    assert r.status_code == 409


def test_part_size_is_pinned():
    """파트 크기가 고정값인지 확인한다. 파트 크기는 중복 감지 해시 계산에 들어가므로 바꾸면 기존 해시와 비교할 수 없다."""
    from api_gateway.services.media.video import _PART_SIZE
    assert _PART_SIZE == 16 * 1024 * 1024
