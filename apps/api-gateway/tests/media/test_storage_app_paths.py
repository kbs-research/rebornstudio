"""앱이 만든 저장소 경로를 원본으로 등록하지 않고, 다른 영상의 원본을 정리하지 않는지 확인하는 테스트.

같은 버킷을 여러 프로젝트가 쓰면 앱 경로 아래에 다른 프로젝트의 산출물이 있다.
`work/` 아래의 분석본 마스터는 비식별화 전 파일이라, 원본으로 등록되면 다른 프로젝트 관리자가 읽는다.
목록 조회는 키가 prefix로 시작하는지만 보므로 `intak`·`t`·빈 prefix도 앱 경로의 키를 돌려준다.
"""
import uuid
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.media import (
    app_paths,
    deident,
    publish,
    storage,
    storage_import,
    transcode,
)
from api_gateway.services.media import video as media_video
from api_gateway.services.media.video_thumbnail import poster_key
from httpx import AsyncClient
from sqlalchemy import select
from tests import _storage
from tests._helpers import new_project


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


def _fake_browse(keys: list[str]):
    """주어진 키를 한 페이지로 돌려주는 가짜 버킷 목록을 만든다."""

    def fake_browse(prefix="", max_keys=500, recursive=False, token=None, bucket=None, store=None):
        """prefix와 관계없이 같은 키 목록을 돌려준다."""
        return {"prefix": prefix, "bucket": _storage.BUCKET, "folders": [],
                "files": [{"key": k, "size": 10} for k in keys],
                "truncated": False, "next_token": None}

    return fake_browse


async def test_prefix_that_reaches_app_paths_is_rejected(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """앱 경로를 포함하거나 앱 경로 안에 있는 prefix와 버킷 루트를 400으로 거절하는지 확인한다.

    빈 문자열은 요청 모델이 422로 막고, `/`는 앞의 `/`를 떼면 버킷 루트가 되어 여기서 막는다.
    """
    monkeypatch.setattr(storage, "browse_bucket", _fake_browse(["sample/a.mp4"]))
    pid = await new_project(client, admin_token, "app-prefix")
    for prefix in ("intak", "transcode", "t", "/", "//", "work/", "archive/x/"):
        r = await client.post(f"/projects/{pid}/storage/register-prefix", headers=_h(admin_token),
                              json={"prefix": prefix, "copyright_policy": "pre_cleared",
                                    "upload_batch_id": str(uuid.uuid4())})
        assert r.status_code == 400, (prefix, r.text)
        assert r.json()["detail"] == "system_prefix", prefix

    r = await client.post(f"/projects/{pid}/storage/register-prefix", headers=_h(admin_token),
                          json={"prefix": "sample/", "copyright_policy": "pre_cleared",
                                "upload_batch_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text


async def test_register_rejects_keys_under_every_app_path(client: AsyncClient, admin_token: str):
    """키 목록 등록이 `work/`·`archive/` 아래 키를 400으로 거절하는지 확인한다."""
    pid = await new_project(client, admin_token, "app-keys")
    vid = uuid.uuid4()
    for key in (f"work/{vid}/analysis-720p.mp4", f"archive/{vid}/source.mp4"):
        r = await client.post(f"/projects/{pid}/storage/register", headers=_h(admin_token),
                              json={"keys": [key], "copyright_policy": "pre_cleared"})
        assert r.status_code == 400, (key, r.text)
        assert r.json()["detail"] == "system_prefix", key


async def test_import_worker_skips_app_keys_in_a_listing(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """폴더째 등록 작업이 목록에 섞인 앱 경로 키를 등록하지 않는지 확인한다.

    큐에 넣은 prefix는 이미 저장돼 있으므로 등록 단계에서도 키마다 다시 거른다.
    """
    other = uuid.uuid4()
    monkeypatch.setattr(storage, "browse_bucket", _fake_browse(
        ["sample/a.mp4", f"work/{other}/analysis-720p.mp4", f"intake/{other}/x.mp4"]))
    pid = await new_project(client, admin_token, "app-worker")
    r = await client.post(f"/projects/{pid}/storage/register-prefix", headers=_h(admin_token),
                          json={"prefix": "sample/", "copyright_policy": "pre_cleared",
                                "upload_batch_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        await storage_import.run_pending_imports(s)
    async with SessionLocal() as s:
        uris = set((await s.scalars(select(Video.source_uri).where(
            Video.project_id == UUID(pid)))).all())
    assert uris == {f"s3://{_storage.BUCKET}/sample/a.mp4"}, uris


async def test_dispose_touches_only_the_videos_own_intake_key(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """원본 정리가 영상 자기 `intake/{video_id}/` 아래 키만 지우는지 확인한다."""
    touched: list[str] = []
    monkeypatch.setattr(storage, "delete_object",
                        lambda *, bucket, key, **_: touched.append(key))
    monkeypatch.setattr(storage, "copy_object",
                        lambda *, bucket, src_key, dst_key, **_: touched.append(src_key))
    pid = await new_project(client, admin_token, "dispose-own")
    other = uuid.uuid4()
    stranger = Video(video_id=uuid.uuid4(), project_id=UUID(pid),
                     source_uri=f"s3://{_storage.BUCKET}/intake/{other}/source")
    async with SessionLocal() as s:
        assert await publish._dispose_source(s, stranger) == {"source": "유지"}
    assert touched == [], "다른 영상의 원본을 정리했다"

    own = Video(video_id=uuid.uuid4(), project_id=UUID(pid))
    own.source_uri = f"s3://{_storage.BUCKET}/intake/{own.video_id}/source"
    async with SessionLocal() as s:
        await publish._dispose_source(s, own)
    assert touched == [f"intake/{own.video_id}/source"]


def test_every_app_written_key_is_under_an_app_prefix():
    """앱이 쓰는 키 생성 함수의 결과가 모두 앱 경로 안에 있는지 확인한다. 새 경로를 빠뜨리면 실패한다."""
    vid = uuid.uuid4()

    class _V:
        """`_upload_key`가 읽는 속성만 가진 영상 대역."""
        video_id = vid

    keys = [
        transcode.analysis_key(vid), transcode.playback_key(vid), transcode.master_key(vid),
        transcode.staging_key(vid, "tok", "x.mp4"), deident.deident_key(vid, "tok"),
        deident.coordinates_staging_key(vid, "tok"), deident.coordinates_key(vid),
        media_video._upload_key(_V()), media_video.archive_key(vid), poster_key(vid),
        f"clip-thumbs/{vid}.jpg", f"projects/{vid}/exports/{vid}/package.zip",
        f"_diagnostics/project-check/{vid}/x.txt",
    ]
    assert [k for k in keys if not app_paths.is_app_key(k)] == []
