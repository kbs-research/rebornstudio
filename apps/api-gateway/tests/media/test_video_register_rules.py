"""영상 행을 만들고 고치는 요청의 규칙 테스트.

- 0바이트·용량 상한 초과·길이 상한 초과 파일은 영상 행을 만들기 전에 거절한다.
  행을 먼저 만들면 끝낼 수 없는 업로드가 "업로드 중"으로 남는다.
- 영상 메타의 방송사·프로그램명·장르·회차·방영일은 null로 지울 수 있다. 제목은 지울 수 없다.
  방영일은 방송이 있을 수 있는 범위만 받는다.
- 저장소에서 등록한 영상도 영상마다 등록 기록이 남는다. 감사 기록은 영상 단위로 읽는다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import Video
from httpx import AsyncClient
from sqlalchemy import text
from tests import _storage
from tests._helpers import events, new_project, upload_video


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _count_videos(pid: str) -> int:
    """프로젝트의 영상 행 수를 센다."""
    async with SessionLocal() as s:
        return await s.scalar(text("select count(*) from videos where project_id = :p"), {"p": pid})


async def test_empty_or_oversized_file_makes_no_row(client: AsyncClient, admin_token: str):
    """0바이트·상한 초과·길이 초과 파일을 422로 거절하고 영상 행을 만들지 않는다."""
    pid = await new_project(client, admin_token, "reg-size")
    url = f"/projects/{pid}/videos"
    cases = [
        ({"file_size_bytes": 0}, "video_file_empty"),
        ({"file_size_bytes": settings.s3_max_upload_bytes + 1}, "video_file_too_large"),
        ({"duration_seconds": settings.probe_max_duration_seconds + 1}, "video_too_long"),
    ]
    for extra, code in cases:
        r = await client.post(url, headers=_h(admin_token), json={"title": "x.mp4", **extra})
        assert r.status_code == 422, (extra, r.text)
        assert r.json()["detail"] == code
    assert await _count_videos(pid) == 0
    ok = await client.post(url, headers=_h(admin_token), json={"title": "ok.mp4", "file_size_bytes": 10})
    assert ok.status_code == 201, ok.text


async def test_multipart_refuses_known_bad_size(client: AsyncClient, admin_token: str):
    """크기가 0으로 등록된 이전 행은 업로드 시작을 422로 거절한다. 끝낼 수 없는 업로드를 시작하지 않는다."""
    pid = await new_project(client, admin_token, "reg-mpu")
    r = await client.post(f"/projects/{pid}/videos", headers=_h(admin_token),
                          json={"title": "legacy.mp4", "file_size_bytes": 10})
    vid = r.json()["video_id"]
    async with SessionLocal() as s:
        (await s.get(Video, UUID(vid))).file_size_bytes = 0
        await s.commit()
    r = await client.post(f"/videos/{vid}/upload/multipart", headers=_h(admin_token))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "video_file_empty"


async def test_meta_fields_can_be_cleared_with_null(client: AsyncClient, admin_token: str):
    """null을 보낸 필드는 지우고 보내지 않은 필드는 그대로 둔다. 제목은 null로 지울 수 없다."""
    pid = await new_project(client, admin_token, "meta-clear")
    vid = await upload_video(client, admin_token, pid)
    aired = "2024-03-01T00:00:00+00:00"
    r = await client.patch(f"/videos/{vid}", headers=_h(admin_token), json={
        "broadcaster": "샘플 방송", "program_name": "9시 뉴스 샘플", "aired_at": aired,
    })
    assert r.status_code == 200, r.text

    r = await client.patch(f"/videos/{vid}", headers=_h(admin_token),
                           json={"aired_at": None, "program_name": None})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["aired_at"] is None and body["program_name"] is None
    assert body["broadcaster"] == "샘플 방송", "보내지 않은 필드가 지워졌다"

    r = await client.patch(f"/videos/{vid}", headers=_h(admin_token), json={"title": None})
    assert r.status_code == 422, r.text


async def test_aired_at_must_be_plausible(client: AsyncClient, admin_token: str):
    """방송이 시작되기 전이나 내일 이후의 방영일을 422로 거절한다."""
    pid = await new_project(client, admin_token, "meta-aired")
    vid = await upload_video(client, admin_token, pid)
    future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    for bad in ("1900-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00", future):
        r = await client.patch(f"/videos/{vid}", headers=_h(admin_token), json={"aired_at": bad})
        assert r.status_code == 422, (bad, r.text)
    r = await client.patch(f"/videos/{vid}", headers=_h(admin_token),
                           json={"aired_at": "1987-06-10T00:00:00+00:00"})
    assert r.status_code == 200, r.text


async def test_storage_registration_logs_each_video(client: AsyncClient, admin_token: str):
    """저장소 등록도 영상마다 등록 기록을 남긴다. 영상 단위 감사 기록이 그 기록으로 시작한다."""

    pid = await new_project(client, admin_token, "reg-log")
    keys = [f"qa-reg-log/{UUID(int=i).hex}.mp4" for i in (1, 2)]
    for key in keys:
        _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")
    r = await client.post(f"/projects/{pid}/storage/register", headers=_h(admin_token),
                          json={"keys": keys, "copyright_policy": "pre_cleared"})
    assert r.status_code == 200, r.text
    vids = [str(v) for v in r.json()["video_ids"]]
    assert len(vids) == 2
    for vid in vids:
        logs = await events(target_id=vid)
        kinds = [e["event_type"] for e in logs]
        assert kinds == ["video.registered"], kinds
        assert logs[0]["payload"]["key"] in keys
