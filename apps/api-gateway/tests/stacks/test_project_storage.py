"""프로젝트별 저장소 대상(저장소 연결·버킷) 테스트.

보장하는 것은 다음과 같다.
- 두 프로젝트가 서로 다른 연결·버킷을 고르면 업로드 서명(단일·멀티파트)·업로드 확인·변환·비식별화·
  재생본 출력·마스터·포스터·클립 썸네일·납품 패키지·문의 첨부가 각자 버킷으로 가고, 읽기(서명 주소·
  재생 소스·매체 결정자)도 각자 버킷에서 한다. 고른 연결의 키로 서명한다.
- 버킷 기록이 없는 행은 어느 버킷으로도 서명하지 않는다. 기본 버킷이 없다.
- NCP 저장소 방식 프로젝트는 저장소 연결과 버킷을 기록해야 영상을 등록한다(409). 샷 검출 방식은
  저장소만 필요하다. 영상이 이미 있는데 기록이 없어도 기본값으로 채우지 않는다.
- 첫 영상부터 바꿀 수 없고(409), 고른 연결은 지울 수 없다(409). 저장한 분석 대상의 워크스페이스
  연동 버킷과 다르면 저장하지 않는다(422). 점검은 버킷 접근·목록·쓰기·업로드 주소·CORS를 확인하고,
  실패하면 사유를 싣는다.

저장소는 인프로세스 moto다. 두 번째 버킷을 만들고, 연결의 키는 moto가 아무 값이나 받는다.
분석 서비스는 가짜 클라이언트(`FakeMaiuClient`)다.
"""
import contextlib
import uuid
from uuid import UUID

import boto3
import pytest
import pytest_asyncio
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import Clip, Export, Project, Video
from api_gateway.services import platform_credentials as pc
from api_gateway.services.exports import export_service
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.media import (
    clip_thumbnail,
    deident,
    publish,
    resolver,
    storage,
    video_thumbnail,
)
from api_gateway.services.media import transcode as transcode_service
from api_gateway.services.media.clip import _thumbnail_url
from api_gateway.services.media.video.queries import _poster_url
from api_gateway.services.stacks import project_stack, project_storage
from httpx import AsyncClient
from sqlalchemy import select
from tests import _storage
from tests._analysis import mi_connection
from tests._helpers import (
    new_project,
    reset_transcode_active,
    seed_clip_work,
    upload_video,
)

#: 두 번째 프로젝트가 쓰는 버킷. 기본 버킷(`_storage.BUCKET`)과 다르다.
_BUCKET_B = "proj-store-b"


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


def _s3():
    """moto에 직접 붙는 boto3 클라이언트를 만든다. 앱 경로를 거치지 않고 객체 위치를 확인한다."""
    return boto3.client("s3", endpoint_url=_storage.ENDPOINT, aws_access_key_id="testing",
                        aws_secret_access_key="testing", region_name="us-east-1")


def _exists(bucket: str, key: str) -> bool:
    """버킷에 객체가 있으면 참."""
    try:
        _s3().head_object(Bucket=bucket, Key=key)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module", autouse=True)
def _bucket_b():
    """두 번째 버킷을 만들고 모듈이 끝나면 지운다.

    moto 서버는 테스트 세션 전체가 같이 쓴다.
    버킷을 남기면 뒤에 도는 저장소 목록 계약(`list_buckets() == ["test-bucket"]`)이 이 버킷까지 보고 실패한다.
    """
    s3 = _s3()
    with contextlib.suppress(Exception):
        s3.create_bucket(Bucket=_BUCKET_B)
    yield
    with contextlib.suppress(Exception):
        for u in s3.list_multipart_uploads(Bucket=_BUCKET_B).get("Uploads") or []:
            s3.abort_multipart_upload(Bucket=_BUCKET_B, Key=u["Key"], UploadId=u["UploadId"])
        for o in s3.list_objects_v2(Bucket=_BUCKET_B).get("Contents") or []:
            s3.delete_object(Bucket=_BUCKET_B, Key=o["Key"])
        s3.delete_bucket(Bucket=_BUCKET_B)


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _reset():
    """테스트마다 연결 메모리·점검 캐시·클라이언트를 처음으로 돌린다."""
    await _storage.reset_memory()
    project_storage._cache.clear()
    project_stack._cache.clear()
    maiu_client.set_client(None)
    yield
    await _storage.reset_memory()
    project_storage._cache.clear()
    project_stack._cache.clear()
    maiu_client.set_client(None)


@pytest_asyncio.fixture(loop_scope="session")
async def fake():
    """가짜 MI를 주입한다. 워크스페이스에는 MI 프로젝트 하나(id 1)가 있고 테스트 저장소가 연동되어 있다."""
    c = FakeMaiuClient()
    c.linked_bucket = _storage.BUCKET
    await c.create_project("가")
    maiu_client.set_client(c)
    yield c
    maiu_client.set_client(None)


async def _ncp_connection(
    client: AsyncClient, token: str, *, access: str, bucket: str, allowed: list[str] | None = None,
) -> str:
    """moto를 가리키는 NCP 저장소 연결을 만들고 저장 점검이 정상인지 확인해 id를 돌려준다.

    `allowed`는 설정의 버킷 밖에서 프로젝트 저장소로 더 허용할 버킷이다.
    """
    r = await client.post("/connections", headers=_h(token), json={
        "name": f"ncp-{uuid.uuid4().hex[:8]}", "provider": "ncp",
        "config": {"endpoint": _storage.ENDPOINT, "bucket": bucket,
                   "region": "kr-standard", "allowed_buckets": allowed or []},
        "access_key": access, "secret_key": "conn-secret",
    })
    assert r.status_code == 201, r.text
    assert r.json()["check"]["status"] == "ok", r.json()
    cid = r.json()["id"]
    return cid


async def _target(pid: str, bucket: str | None = None) -> dict:
    """테스트 저장소 연결과 버킷을 저장소 대상 요청 본문으로 만든다."""
    return {"storage_connection_id": await _storage.connection_for(pid),
            "bucket": bucket or _storage.BUCKET}


async def _project_b(client: AsyncClient, token: str, name: str, **kw) -> tuple[str, str]:
    """두 번째 버킷과 그 버킷의 연결을 고른 프로젝트를 만들고 `(project_id, 연결 id)`를 돌려준다."""
    cid = await _ncp_connection(client, token, access="key-b", bucket=_BUCKET_B)
    pid = await new_project(client, token, name, **kw)
    r = await client.put(f"/projects/{pid}/storage-stack", headers=_h(token),
                         json={"storage_connection_id": cid, "bucket": _BUCKET_B})
    assert r.status_code == 200, r.text
    return pid, cid


async def _video(vid: str) -> Video:
    """영상 행을 읽는다."""
    async with SessionLocal() as s:
        return await s.get(Video, UUID(vid))


def _signed_key(url: str) -> str:
    """서명 주소에서 액세스 키를 꺼낸다."""
    return url.split("X-Amz-Credential=")[1].split("%2F")[0].split("/")[0]


def _enable(monkeypatch) -> None:
    """외부 연동이 켜진 서버를 흉내 낸다. 분석은 가짜 클라이언트가 응답한다."""
    monkeypatch.setattr(settings, "maiu_enabled", True)


# 두 버킷


async def test_uploads_go_to_each_projects_bucket(client: AsyncClient, admin_token: str):
    """업로드 서명·멀티파트·업로드 확인이 프로젝트가 고른 버킷과 연결의 키로 가는지 확인한다."""
    pa = await new_project(client, admin_token, "st-a")
    pb, cid = await _project_b(client, admin_token, "st-b")

    va, vb = await upload_video(client, admin_token, pa), await upload_video(client, admin_token, pb)
    assert (await _video(va)).source_uri.startswith(f"s3://{_storage.BUCKET}/")
    vrow = await _video(vb)
    assert vrow.source_uri == f"s3://{_BUCKET_B}/intake/{vb}/source", vrow.source_uri
    assert vrow.extra["storage"] == {"connection_id": cid, "bucket": _BUCKET_B}
    assert _exists(_BUCKET_B, f"intake/{vb}/source")
    assert not _exists(_storage.BUCKET, f"intake/{vb}/source")

    # 멀티파트는 시작·파트 서명·합치기가 모두 그 버킷이다.
    vid = (await client.post(f"/projects/{pb}/videos", headers=_h(admin_token),
                             json={"title": "mpu", "copyright_policy": "pre_cleared"})).json()
    vid = vid["video_id"]
    mpu = (await client.post(f"/videos/{vid}/upload/multipart", headers=_h(admin_token))).json()
    uploads = _s3().list_multipart_uploads(Bucket=_BUCKET_B).get("Uploads") or []
    assert any(u["UploadId"] == mpu["upload_id"] for u in uploads), "다른 버킷에서 시작했다"
    parts = (await client.post(f"/videos/{vid}/upload/parts", headers=_h(admin_token),
                               json={"upload_id": mpu["upload_id"], "part_numbers": [1]})).json()
    url = parts["urls"]["1"]
    assert f"/{_BUCKET_B}/" in url and _signed_key(url) == "key-b", url
    import httpx
    async with httpx.AsyncClient() as c:
        assert (await c.put(url, content=b"x" * 64)).status_code == 200
    r = await client.post(f"/videos/{vid}/upload/complete", headers=_h(admin_token),
                          json={"upload_id": mpu["upload_id"]})
    assert r.status_code == 200, r.text
    assert (await _video(vid)).source_uri.startswith(f"s3://{_BUCKET_B}/")


async def test_derived_outputs_and_reads_follow_the_project_bucket(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """변환·비식별화·재생본 출력과 마스터·재생 소스·포스터·클립 썸네일이 프로젝트 버킷을 쓰는지 확인한다."""
    await reset_transcode_active()
    pb, _ = await _project_b(client, admin_token, "st-derived")
    vid = await upload_video(client, admin_token, pb)
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.extra = {**(v.extra or {}), "probe": {"video_codec": "av1", "height": 1080}}
        v.transcode_status = "pending"
        await s.commit()
        jobs = await transcode_service.build_jobs(s)
    job = next(j for j in jobs if j["video_id"] == vid)
    put = job["targets"][0]["put_url"]
    assert f"/{_BUCKET_B}/" in put and _signed_key(put) == "key-b", put
    assert f"/{_BUCKET_B}/" in job["input_url"]
    # 워커가 그 시도의 스테이징 키에 마스터를 올린 상태를 만든다.
    _s3().put_object(Bucket=_BUCKET_B, Body=b"m",
                     Key=transcode_service.staging_key(UUID(vid), job["token"],
                                                       "analysis-720p.mp4"))
    async with SessionLocal() as s:
        assert await transcode_service.complete_transcode(s, UUID(vid), job["token"], ok=True)
        v = await s.get(Video, UUID(vid))
        assert v.extra["transcode"]["bucket"] == _BUCKET_B
        assert resolver.media_uri(v, "deident_input").startswith(f"s3://{_BUCKET_B}/work/")

        # 비식별화 잡의 출력·좌표도 같은 버킷이다.
        p = await s.get(Project, v.project_id)
        p.deident_enabled = True
        v.deident_status = "pending"
        await s.commit()
        djobs = await deident.build_jobs(s)
    djob = next(j for j in djobs if j["video_id"] == vid)
    assert f"/{_BUCKET_B}/" in djob["put_url"] and f"/{_BUCKET_B}/" in djob["input_url"]
    assert _signed_key(djob["put_url"]) == "key-b"

    # 재생본은 그 버킷의 마스터에서 만들어 그 버킷에 둔다. 마스터 객체를 실제로 둔다.
    _s3().put_object(Bucket=_BUCKET_B, Key=transcode_service.master_key(UUID(vid)), Body=b"m")
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.deident_status = "skipped"
        v.publish_status = "pending"
        await s.commit()
        pjobs = await publish.build_jobs(s)
    pjob = next(j for j in pjobs if j["video_id"] == vid)
    assert f"/{_BUCKET_B}/" in pjob["input_url"]
    assert f"/{_BUCKET_B}/" in pjob["targets"][0]["put_url"]
    _s3().put_object(Bucket=_BUCKET_B, Body=b"p",
                     Key=transcode_service.staging_key(UUID(vid), pjob["token"], "play-480p.mp4"))
    async with SessionLocal() as s:
        assert await publish.complete(s, UUID(vid), pjob["token"], ok=True)
        v = await s.get(Video, UUID(vid))
    assert v.analysis_uri == f"s3://{_BUCKET_B}/{transcode_service.analysis_key(UUID(vid))}"
    src = resolver.playback_source(v)
    assert src is not None and src.bucket == _BUCKET_B

    # 포스터와 클립 썸네일은 영상의 버킷에 올리고 그 버킷을 기록해 그 버킷으로 서명한다.
    # 비식별화를 켠 프로젝트라 비식별화를 마친 영상이어야 썸네일을 추출한다(`thumbnail_source`).
    monkeypatch.setattr(clip_thumbnail, "_extract_frame", lambda url, ts: b"\xff\xd8jpeg")
    async with SessionLocal() as s:
        (await s.get(Video, UUID(vid))).deident_status = "done"
        await s.commit()
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        assert await video_thumbnail._process_one(s, v)
    v = await _video(vid)
    assert v.extra[video_thumbnail.BUCKET_KEY] == _BUCKET_B
    assert _exists(_BUCKET_B, v.extra["poster_thumbnail_key"])
    assert f"/{_BUCKET_B}/" in (_poster_url(v) or "")
    clip_id, _ = await seed_clip_work(pb, vid)
    async with SessionLocal() as s:
        clip = await s.get(Clip, UUID(clip_id))
        v = await s.get(Video, UUID(vid))
        assert await clip_thumbnail._process_one(s, clip, v)
        await s.commit()
    async with SessionLocal() as s:
        clip = await s.get(Clip, UUID(clip_id))
    assert clip.extra[clip_thumbnail.BUCKET_KEY] == _BUCKET_B
    assert _exists(_BUCKET_B, clip.extra["scene_thumbnail_key"])
    assert f"/{_BUCKET_B}/" in (_thumbnail_url(clip.extra) or "")


async def test_export_package_uses_the_project_bucket(
    client: AsyncClient, admin_token: str,
):
    """납품 패키지를 프로젝트 버킷에 올리고 내려받기 주소도 그 버킷으로 서명하는지 확인한다."""
    pb, _ = await _project_b(client, admin_token, "st-export")
    r = await client.post(f"/projects/{pb}/exports", headers=_h(admin_token), json={})
    assert r.status_code in (200, 201, 202), r.text
    eid = r.json()["export_id"]
    async with SessionLocal() as s:
        await export_service.process_pending(s, limit=10)
    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
    assert row.status == "completed", row.error
    assert row.storage_bucket == _BUCKET_B
    assert _exists(_BUCKET_B, row.manifest_s3_key)
    d = await client.get(f"/exports/{eid}/download", headers=_h(admin_token))
    assert d.status_code == 200, d.text
    assert f"/{_BUCKET_B}/" in d.json()["download_url"]


async def test_rows_without_a_bucket_record_are_not_signed_from_a_default(
    client: AsyncClient, admin_token: str,
):
    """버킷 기록이 없는 행을 어느 버킷으로도 읽지 않고 409나 빈 주소로 끝내는지 확인한다. 기본 버킷이 없다.

    이행(마이그레이션 0091)이 이전 행의 버킷을 채운다.
    채우지 못한 행을 아무 버킷으로 서명하면 고르지 않은 계정의 객체를 내준다.
    """
    v = Video(video_id=uuid.uuid4(), extra={"poster_thumbnail_key": "video-posters/old.jpg",
                                            "transcode": {"master_key": "k"}})
    with pytest.raises(pc.CredentialsMissingError):
        transcode_service.derived_bucket(v)
    assert _poster_url(v) is None
    assert _thumbnail_url({"scene_thumbnail_key": "c.jpg"}) is None
    assert storage.presign_get_or_none("projects/x/package.zip", bucket=None) is None
    assert storage.presign_get_or_none("projects/x/package.zip", bucket="unknown-bucket") is None
    with pytest.raises(pc.CredentialsMissingError):
        storage.presign_get_for(bucket="unknown-bucket", key="k")


# 등록 시트의 버킷


async def test_ingest_sheet_accepts_only_the_project_bucket(
    client: AsyncClient, admin_token: str,
):
    """등록과 폴더 등록이 프로젝트 저장소의 버킷만 받고 다른 버킷을 422로 막는지 확인한다.

    계정에는 두 버킷이 있다.
    다른 버킷의 원본을 받으면 프로젝트 연결의 키로 읽지 못해 규격 검증·변환이 등록 뒤에 실패한다.
    """
    h = _h(admin_token)
    pb, _ = await _project_b(client, admin_token, "st-sheet-b")
    pa = await new_project(client, admin_token, "st-sheet-a")
    for pid, mine, other in ((pb, _BUCKET_B, _storage.BUCKET),
                             (pa, _storage.BUCKET, _BUCKET_B)):
        _s3().put_object(Bucket=mine, Key="raw/a.mp4", Body=b"x")
        r = await client.post(f"/projects/{pid}/storage/register", headers=h, json={
            "keys": ["raw/a.mp4"], "copyright_policy": "pre_cleared", "bucket": other})
        assert r.status_code == 422 and r.json()["detail"] == "storage_bucket_not_project"
        r = await client.post(f"/projects/{pid}/storage/register-prefix", headers=h, json={
            "prefix": "raw/", "copyright_policy": "pre_cleared", "bucket": other,
            "upload_batch_id": str(uuid.uuid4())})
        assert r.status_code == 422 and r.json()["detail"] == "storage_bucket_not_project"

        r = await client.post(f"/projects/{pid}/storage/register", headers=h, json={
            "keys": ["raw/a.mp4"], "copyright_policy": "pre_cleared", "bucket": mine})
        assert r.status_code == 200, r.text
        vid = r.json()["video_ids"][0]
        assert (await _video(vid)).source_uri == f"s3://{mine}/raw/a.mp4"


# 기록 필수·잠금·고정 기록


async def test_ncp_projects_need_a_storage_target_and_shots_need_only_storage(
    client: AsyncClient, admin_token: str, monkeypatch, fake,
):
    """저장소 대상이 없으면 등록 세 경로가 409이고 샷 검출은 저장소만 있으면 되는지 확인한다.

    선택지는 처음 값을 주지 않고, 연결을 고르기 전에는 버킷 목록도 읽지 않는다.
    """
    _enable(monkeypatch)
    h = _h(admin_token)
    shots = await new_project(client, admin_token, "st-shots", analysis_preset="ncp-shots",
                              storage=False)
    p = (await client.get(f"/projects/{shots}", headers=h)).json()
    assert p["storage_stack_missing"] is True and p["analysis_stack_missing"] is False
    r = await client.post(f"/projects/{shots}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 409 and r.json()["detail"] == "storage_stack_unset"
    r = await client.post(f"/projects/{shots}/storage/register", headers=h,
                          json={"keys": ["raw/a.mp4"], "copyright_policy": "pre_cleared"})
    assert r.status_code == 409 and r.json()["detail"] == "storage_stack_unset"
    r = await client.post(f"/projects/{shots}/storage/register-prefix", headers=h, json={
        "prefix": "raw/", "copyright_policy": "pre_cleared",
        "upload_batch_id": str(uuid.uuid4())})
    assert r.status_code == 409 and r.json()["detail"] == "storage_stack_unset"

    c = (await client.get(f"/projects/{shots}/storage-choices", headers=h)).json()
    assert c["required"] is True and c["selected"]["bucket"] is None
    assert "default" not in c and c["buckets"] is None, "연결을 고르기 전에 무언가를 채웠다"
    c = (await client.get(f"/projects/{shots}/storage-choices", headers=h,
                          params={"connection_id": await _storage.connection_for(shots)})).json()
    assert _storage.BUCKET in (c["buckets"] or []), c
    r = await client.put(f"/projects/{shots}/storage-stack", headers=h,
                         json={"bucket": _storage.BUCKET})
    assert r.status_code == 422 and r.json()["detail"] == "storage_stack_incomplete"
    r = await client.put(f"/projects/{shots}/storage-stack", headers=h,
                         json=await _target(shots))
    assert r.status_code == 200, r.text
    assert (await client.get(f"/projects/{shots}", headers=h)).json()["storage_stack_missing"] \
        is False
    r = await client.post(f"/projects/{shots}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 201, "샷 검출은 분석 대상 없이 저장소만으로 등록한다"
    r = await client.put(f"/projects/{shots}/analysis-stack", headers=h,
                         json={"maiu_workspace": "fake", "maiu_project_id": "1"})
    assert r.status_code == 422 and r.json()["detail"] == "analysis_stack_unsupported"


async def test_locked_from_the_first_video_and_the_connection_cannot_be_deleted(
    client: AsyncClient, admin_token: str,
):
    """영상이 생기면 저장소 대상을 바꾸거나 지울 수 없고(같은 값은 다시 저장 가능) 고른 연결은 삭제가 409인지 확인한다."""
    h = _h(admin_token)
    pb, cid = await _project_b(client, admin_token, "st-lock")
    r = await client.post(f"/projects/{pb}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 201, r.text
    r = await client.put(f"/projects/{pb}/storage-stack", headers=h, json=await _target(pb))
    assert r.status_code == 409 and r.json()["detail"] == "storage_stack_locked"
    r = await client.put(f"/projects/{pb}/storage-stack", headers=h, json={})
    assert r.status_code == 409, "잠긴 기록을 지울 수 있었다"
    r = await client.put(f"/projects/{pb}/storage-stack", headers=h,
                         json={"storage_connection_id": cid, "bucket": _BUCKET_B})
    assert r.status_code == 200, "같은 값을 다시 저장하는 것은 막지 않는다"
    c = (await client.get(f"/projects/{pb}/storage-choices", headers=h)).json()
    assert c["locked"] is True and c["video_count"] == 1 and c["deleted_video_count"] == 0

    conns = (await client.get("/connections", headers=h)).json()
    mine = next(x for x in conns if x["id"] == cid)
    assert any("st-lock" in name for name in mine["used_by"]), mine
    r = await client.delete(f"/connections/{cid}", headers=h)
    assert r.status_code == 409 and r.json()["detail"] == "connection_in_use"


async def test_lock_count_names_the_deleted_videos(
    client: AsyncClient, admin_token: str, fake,
):
    """지운 영상도 잠금 근거로 세고 그중 지운 수를 따로 주는지 확인한다. 화면이 두 수로 분모를 밝힌다.

    영상 목록과 작업 단계는 지운 영상을 세지 않는다.
    잠금 문구가 지운 영상까지 센 수만 표시하면 같은 화면의 영상 수와 어긋난다.
    저장소 대상과 분석 대상이 같은 수를 준다.
    """
    h = _h(admin_token)
    pid = await new_project(client, admin_token, "st-lock-del")
    kept = await upload_video(client, admin_token, pid)
    gone = await upload_video(client, admin_token, pid)
    assert kept != gone
    r = await client.delete(f"/videos/{gone}", headers=h)
    assert r.status_code == 204, r.text
    for path in ("storage-choices", "analysis-choices"):
        c = (await client.get(f"/projects/{pid}/{path}", headers=h)).json()
        assert c["locked"] is True, path
        assert (c["video_count"], c["deleted_video_count"]) == (2, 1), (path, c)


async def test_bucket_must_match_the_saved_analysis_workspace(
    client: AsyncClient, admin_token: str, fake,
):
    """분석 대상을 이미 저장했으면 그 워크스페이스 연동 버킷과 다른 버킷을 저장하지 않는지 확인한다.

    반대로 분석 대상 저장도 프로젝트 버킷과 같은 워크스페이스만 받는다.
    """
    h = _h(admin_token)
    mi = await mi_connection(client, admin_token)
    analysis = {"analysis_connection_id": mi, "maiu_workspace": "fake", "maiu_project_id": "1"}
    cid_b = await _ncp_connection(client, admin_token, access="key-b", bucket=_BUCKET_B)
    target_b = {"storage_connection_id": cid_b, "bucket": _BUCKET_B}
    pid = await new_project(client, admin_token, "st-mi")
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=h, json=analysis)
    assert r.status_code == 200, r.text
    r = await client.put(f"/projects/{pid}/storage-stack", headers=h, json=target_b)
    assert r.status_code == 422 and r.json()["detail"] == "maiu_bucket_mismatch"

    other = await new_project(client, admin_token, "st-mi2")
    r = await client.put(f"/projects/{other}/storage-stack", headers=h, json=target_b)
    assert r.status_code == 200, r.text
    r = await client.put(f"/projects/{other}/analysis-stack", headers=h, json=analysis)
    assert r.status_code == 422 and r.json()["detail"] == "maiu_bucket_mismatch"
    fake.linked_bucket = _BUCKET_B
    r = await client.put(f"/projects/{other}/analysis-stack", headers=h, json=analysis)
    assert r.status_code == 200, r.text


async def test_locked_project_answers_locked_before_the_bucket_mismatch(
    client: AsyncClient, admin_token: str, fake,
):
    """잠긴 프로젝트에 다른 버킷을 보내면 버킷 불일치(422)가 아니라 잠김(409)으로 거절하는지 확인한다.

    잠긴 프로젝트는 어떤 값을 보내도 바꿀 수 없다. 불일치를 먼저 알리면 버킷만 맞추면 될 것처럼 읽힌다.
    """
    h = _h(admin_token)
    mi = await mi_connection(client, admin_token)
    pid = await new_project(client, admin_token, "st-mi-lock")
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=h, json={
        "analysis_connection_id": mi, "maiu_workspace": "fake", "maiu_project_id": "1"})
    assert r.status_code == 200, r.text
    await upload_video(client, admin_token, pid)
    r = await client.put(f"/projects/{pid}/storage-stack", headers=h,
                         json=await _target(pid, _BUCKET_B))
    assert r.status_code == 409 and r.json()["detail"] == "storage_stack_locked", r.text
    r = await client.put(f"/projects/{pid}/storage-stack", headers=h, json=await _target(pid))
    assert r.status_code == 200, "같은 값을 다시 보내는 것은 막지 않는다"


# 점검


async def test_check_reports_each_permission_and_save_refuses_a_missing_bucket(
    client: AsyncClient, admin_token: str,
):
    """점검이 다섯 항목을 확인하고 없는 버킷은 사유와 함께 저장하지 않는지 확인한다."""
    h = _h(admin_token)
    pb, _ = await _project_b(client, admin_token, "st-check")
    report = (await client.post(f"/projects/{pb}/storage-stack/check", headers=h)).json()
    by = {c["key"]: c for c in report["checks"]}
    assert set(by) == {"storage_bucket", "storage_list", "storage_write", "storage_presign",
                       "storage_cors"}
    assert report["failed"] == 0, report
    assert by["storage_cors"]["status"] == "warn", "CORS 규칙이 없는데 정상이라고 했다"

    _s3().put_bucket_cors(Bucket=_BUCKET_B, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["http://test"], "AllowedMethods": ["PUT"],
        "AllowedHeaders": ["*"]}]})
    report = (await client.post(f"/projects/{pb}/storage-stack/check", headers=h)).json()
    cors = next(c for c in report["checks"] if c["key"] == "storage_cors")
    assert cors["status"] == "ok", cors

    pid = await new_project(client, admin_token, "st-nobucket")
    # 없는 버킷도 허용 범위 안이어야 점검까지 간다. 범위 밖은 점검 전에 막힌다.
    loose = await _ncp_connection(client, admin_token, access="key-x", bucket=_storage.BUCKET,
                                  allowed=["no-such-bucket-x"])
    r = await client.put(f"/projects/{pid}/storage-stack", headers=h,
                         json={"storage_connection_id": loose, "bucket": "no-such-bucket-x"})
    assert r.status_code == 422 and r.json()["detail"] == "storage_bucket_not_found"
    r = await client.put(f"/projects/{pid}/storage-stack", headers=h,
                         json=await _target(pid, "A_B"))
    assert r.status_code == 422 and r.json()["detail"] == "storage_bucket_invalid"


async def test_any_origin_cors_is_a_warning_that_does_not_block_saving(
    client: AsyncClient, admin_token: str,
):
    """버킷 CORS가 모든 출처를 허용하면 점검이 경고와 안내를 싣고, 저장은 막지 않는지 확인한다."""
    h = _h(admin_token)
    pb, cid = await _project_b(client, admin_token, "st-cors-any")
    _s3().put_bucket_cors(Bucket=_BUCKET_B, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["*"], "AllowedMethods": ["PUT", "GET"], "AllowedHeaders": ["*"]}]})
    try:
        report = (await client.post(f"/projects/{pb}/storage-stack/check", headers=h)).json()
        cors = next(c for c in report["checks"] if c["key"] == "storage_cors")
        assert cors["status"] == "warn", cors
        assert "모든 주소" in cors["detail"] and "웹 주소" in (cors["hint"] or ""), cors
        assert report["failed"] == 0, report

        pid = await new_project(client, admin_token, "st-cors-any-save")
        r = await client.put(f"/projects/{pid}/storage-stack", headers=h,
                             json={"storage_connection_id": cid, "bucket": _BUCKET_B})
        assert r.status_code == 200, r.text
    finally:
        _s3().delete_bucket_cors(Bucket=_BUCKET_B)


async def test_failed_check_blocks_registration_until_fixed(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """기록한 버킷이 사라지면 등록 직전 점검이 사유와 함께 409로 막고 점검 결과에 실패를 남기며, 있는 버킷으로 고치면 다시 받는지 확인한다."""
    _enable(monkeypatch)
    h = _h(admin_token)
    shots = await new_project(client, admin_token, "st-gone", analysis_preset="ncp-shots")
    # 사라진 버킷도 허용 범위 안에 있던 버킷이다. 범위 밖이면 점검 전에 다른 사유로 막힌다.
    loose = await _ncp_connection(client, admin_token, access="key-x", bucket=_storage.BUCKET,
                                  allowed=["vanished-bucket-x"])
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(shots))
        p.settings = pc.with_storage_target(
            p.settings, pc.StorageTarget(loose, "vanished-bucket-x"))
        await s.commit()
    r = await client.post(f"/projects/{shots}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 409 and r.json()["detail"] == "저장소가 없습니다"
    report = (await client.post(f"/projects/{shots}/storage-stack/check", headers=h)).json()
    first = report["checks"][0]
    assert first["key"] == "storage_bucket" and first["status"] == "fail"
    assert all(c["status"] == "unchecked" for c in report["checks"][1:])

    # 있는 버킷으로 고치면 다시 점검이 통과하고 같은 등록이 받아들여진다.
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(shots))
        p.settings = pc.with_storage_target(
            p.settings, pc.StorageTarget(_storage.connection_id(), _storage.BUCKET))
        await s.commit()
    report = (await client.post(f"/projects/{shots}/storage-stack/check", headers=h)).json()
    assert report["failed"] == 0, report
    r = await client.post(f"/projects/{shots}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 201, r.text


async def test_periodic_check_records_the_storage_failure(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """주기 점검이 저장소 대상 실패를 따로 기록하고 같은 결과가 이어지면 다시 기록하지 않는지 확인한다."""
    from api_gateway.models import DomainEvent

    shots = await new_project(client, admin_token, "st-periodic", analysis_preset="ncp-shots")
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(shots))
        p.settings = pc.with_storage_target(
            p.settings, pc.StorageTarget(_storage.connection_id(), "gone-bucket-y"))
        await s.commit()
    _enable(monkeypatch)
    monkeypatch.setattr(project_stack, "_recheck", _only_bucket("gone-bucket-y"))
    async with SessionLocal() as s:
        assert await project_stack.recheck_all(s) == 1
        assert await project_stack.recheck_all(s) == 0
        rows = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == project_storage.HEALTH_EVENT,
            DomainEvent.target_id == UUID(shots)))).all()
    assert [r.payload["after"] for r in rows] == ["fail"]


def _only_bucket(bucket: str):
    """주기 점검을 그 버킷을 고른 프로젝트로 좁힌다. 같은 DB에 있는 다른 테스트의 프로젝트를 점검하지 않는다."""
    inner = project_stack._recheck

    async def _wrapped(db, column, kind, supported, run):
        """`supported`에 버킷 조건을 더해 원래 함수를 부른다."""
        def _mine(settings_):
            """그 버킷을 고른 프로젝트 설정이면 참."""
            return supported(settings_) and (settings_ or {}).get("stack", {}).get(
                "bucket") == bucket

        return await inner(db, column, kind, _mine, run)
    return _wrapped


async def test_locked_project_without_a_record_stays_blocked(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """영상이 있는데 대상 기록이 없는 프로젝트를 막혔다고 표시하고 등록은 409인지 확인한다. 기본값으로 채우지 않는다.

    이행이 채우지 못한 프로젝트다. 다른 버킷으로 조용히 넘어가지 않고 멈춰서 알린다.
    """
    h = _h(admin_token)
    pid = await new_project(client, admin_token, "st-locked-unset", analysis_preset="ncp-shots")
    r = await client.post(f"/projects/{pid}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 201, r.text
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(pid))
        p.settings = pc.with_storage_target(p.settings, None)
        await s.commit()
    p = (await client.get(f"/projects/{pid}", headers=h)).json()
    assert p["storage_stack_missing"] is True, p
    listed = next(x for x in (await client.get("/projects", headers=h)).json()
                  if x["project_id"] == pid)
    assert listed["storage_stack_missing"] is True
    r = await client.post(f"/projects/{pid}/videos", headers=h,
                          json={"title": "t2", "copyright_policy": "pre_cleared"})
    assert r.status_code == 409 and r.json()["detail"] == "storage_stack_unset"
    async with SessionLocal() as s:
        assert pc.storage_target_of((await s.get(Project, UUID(pid))).settings) is None
