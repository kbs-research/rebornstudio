"""영상 등록 요청이 서버가 쓰는 `extra` 키를 넣지 못하는지 확인하는 테스트.

`extra`의 포스터 키·버킷과 저장소 고정 기록은 서버가 서명할 객체를 정한다.
등록 요청이 이 값을 넣을 수 있으면 프로젝트 관리자가 다른 버킷의 객체 주소를 서명받는다.
포스터 주소는 영상의 고정 버킷과 썸네일 워커가 만드는 키 형식이 모두 맞을 때만 서명한다.
"""
import uuid
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.media import storage
from api_gateway.services.media.video.queries import _poster_url
from httpx import AsyncClient
from tests import _storage
from tests._helpers import new_project


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def test_create_ignores_server_owned_metadata(client: AsyncClient, admin_token: str):
    """등록 요청의 `metadata`를 저장하지 않고 포스터 주소도 서명하지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "meta-iso")
    r = await client.post(f"/projects/{pid}/videos", headers=_h(admin_token), json={
        "title": "meta-iso",
        "metadata": {
            "poster_thumbnail_key": "projects/other/package.zip",
            "poster_thumbnail_bucket": _storage.BUCKET,
            "storage": {"connection_id": str(uuid.uuid4()), "bucket": "other-bucket"},
            "transcode": {"bucket": "other-bucket", "master_key": "x/master.mp4"},
        },
    })
    assert r.status_code == 201, r.text
    body = r.json()
    got = await client.get(f"/videos/{body['video_id']}", headers=_h(admin_token))
    assert got.status_code == 200, got.text
    assert got.json()["thumbnail_url"] is None, "요청이 넣은 포스터 키로 주소를 서명했다"
    async with SessionLocal() as s:
        extra = (await s.get(Video, UUID(body["video_id"]))).extra
    assert "poster_thumbnail_key" not in extra
    assert "transcode" not in extra
    assert extra["storage"]["bucket"] == _storage.BUCKET, "요청이 저장소 고정 기록을 바꿨다"


def _video(extra: dict) -> Video:
    """`extra`만 채운 영상 행을 만든다. DB에 넣지 않는다."""
    return Video(video_id=uuid.uuid4(), extra=extra)


def test_poster_is_signed_only_for_the_pinned_bucket_and_worker_key(monkeypatch):
    """포스터 버킷이 고정 버킷과 다르거나 키가 썸네일 워커 형식이 아니면 서명하지 않는지 확인한다."""
    signed: list[tuple[str, str | None]] = []

    def fake_presign(key: str, *, bucket: str | None) -> str:
        """서명 요청을 기록하고 가짜 주소를 돌려준다."""
        signed.append((key, bucket))
        return f"https://signed/{bucket}/{key}"

    monkeypatch.setattr(storage, "presign_get_or_none", fake_presign)
    pin = {"connection_id": "c1", "bucket": "pinned"}

    good = _video({})
    good.extra = {"storage": pin, "poster_thumbnail_bucket": "pinned",
                  "poster_thumbnail_key": f"video-posters/{good.video_id}.jpg"}
    assert _poster_url(good) == f"https://signed/pinned/video-posters/{good.video_id}.jpg"

    other_bucket = _video({})
    other_bucket.extra = {"storage": pin, "poster_thumbnail_bucket": "elsewhere",
                          "poster_thumbnail_key": f"video-posters/{other_bucket.video_id}.jpg"}
    assert _poster_url(other_bucket) is None

    foreign_key = _video({"storage": pin, "poster_thumbnail_bucket": "pinned",
                          "poster_thumbnail_key": "projects/x/package.zip"})
    assert _poster_url(foreign_key) is None

    other_video = _video({"storage": pin, "poster_thumbnail_bucket": "pinned",
                          "poster_thumbnail_key": f"video-posters/{uuid.uuid4()}.jpg"})
    assert _poster_url(other_video) is None

    no_pin = _video({"poster_thumbnail_bucket": "pinned",
                     "poster_thumbnail_key": "video-posters/x.jpg"})
    assert _poster_url(no_pin) is None
    assert len(signed) == 1
