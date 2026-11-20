"""시도마다 다른 스테이징 키 테스트. 회수된 시도의 늦은 업로드가 승격된 파일을 바꾸지 못하는지 본다.

잡 URL은 시도가 끝난 뒤에도 유효 기간 동안 쓸 수 있다. 펜싱은 낡은 완료 보고를 버리지만
업로드 자체는 막지 못한다. 키가 시도마다 같으면 낡은 업로드가 마스터를 덮어써
비식별화를 거친 마스터가 마스킹 전 파일로 바뀐다.

워커는 presigned PUT으로 올리는 일만 한다. 여기서는 그 PUT을 httpx로 직접 보낸다.
"""
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Project, Video
from api_gateway.services.media import deident as deident_service
from api_gateway.services.media import storage
from api_gateway.services.media import transcode as transcode_service
from api_gateway.services.media.pipeline import open_next
from httpx import AsyncClient
from sqlalchemy import update
from tests import _storage
from tests._helpers import new_project, reset_transcode_active, upload_video


@pytest.fixture(autouse=True)
async def _isolate():
    """앞 테스트가 남긴 변환·publish·비식별화 활성 행을 비운다."""
    await reset_transcode_active()
    yield


def _put(url: str, body: bytes, content_type: str = "video/mp4") -> None:
    """워커처럼 presigned PUT으로 산출을 올린다."""
    r = httpx.put(url, content=body, headers={"Content-Type": content_type}, timeout=10)
    assert r.status_code < 300, r.text


def _read(key: str) -> bytes:
    """테스트 버킷의 객체 내용을 읽는다."""
    return storage.get_object_body(bucket=_storage.BUCKET, key=key).read()


async def _reclaim(vid: uuid.UUID, stage: str) -> None:
    """그 단계의 리스를 만료시키고 회수를 돌린다. 워커가 응답하지 않는 상태와 같다."""
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == vid).values(
            **{f"{stage}_lease_until": datetime.now(UTC) - timedelta(seconds=1)}))
        await s.commit()
    service = transcode_service if stage == "transcode" else deident_service
    async with SessionLocal() as s:
        assert await service.reclaim_expired(s) >= 1


async def test_a_late_transcode_upload_cannot_replace_the_promoted_master(
    client: AsyncClient, admin_token: str,
):
    """회수된 변환 시도가 늦게 올린 파일은 그 시도의 키에만 남고 마스터는 그대로다."""
    pid = await new_project(client, admin_token, "stage-token-tx")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": {"video_codec": "av1", "height": 1080}}
        v.transcode_status = "pending"
        await s.commit()
        old = next(j for j in await transcode_service.build_jobs(s)
                   if j["video_id"] == str(vid))
    await _reclaim(vid, "transcode")
    async with SessionLocal() as s:
        new = next(j for j in await transcode_service.build_jobs(s)
                   if j["video_id"] == str(vid))
    assert old["targets"][0]["put_url"] != new["targets"][0]["put_url"]

    _put(new["targets"][0]["put_url"], b"new-master")
    async with SessionLocal() as s:
        assert await transcode_service.complete_transcode(s, vid, new["token"], ok=True)
    master = transcode_service.master_key(vid)
    assert _read(master) == b"new-master"

    # 회수된 시도의 워커가 아직 유효한 URL로 늦게 올린다.
    _put(old["targets"][0]["put_url"], b"stale-master")
    assert _read(master) == b"new-master", "늦은 업로드가 마스터를 덮어썼다"
    stale = transcode_service.staging_key(vid, old["token"], "analysis-720p.mp4")
    assert _read(stale) == b"stale-master"
    # 낡은 시도가 성공을 보고하면 펜싱이 버리고 그 시도의 파일을 지운다. 마스터는 그대로다.
    async with SessionLocal() as s:
        assert not await transcode_service.complete_transcode(s, vid, old["token"], ok=True)
    assert storage.object_exists(bucket=_storage.BUCKET, key=stale) is False
    assert _read(master) == b"new-master"


async def test_a_late_deident_upload_cannot_replace_the_masked_master(
    client: AsyncClient, admin_token: str,
):
    """회수된 비식별화 시도가 늦게 올린 파일은 마스킹된 마스터와 좌표를 바꾸지 못한다."""
    pid = await new_project(client, admin_token, "stage-token-deid")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        (await s.get(Project, uuid.UUID(pid))).deident_enabled = True
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": {
            "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"}}
        await s.commit()
    async with SessionLocal() as s:
        await open_next(s, await s.get(Video, vid))
        await s.commit()
        old = next(j for j in await deident_service.build_jobs(s)
                   if j["video_id"] == str(vid))
    await _reclaim(vid, "deident")
    async with SessionLocal() as s:
        new = next(j for j in await deident_service.build_jobs(s)
                   if j["video_id"] == str(vid))

    _put(new["coordinates_put_url"], b'{"frames":[1]}', "application/json")
    _put(new["put_url"], b"masked")
    report = {"model": "essential-yunet", "masked": True,
              "coordinates": {"uploaded": True, "schema_version": 1,
                              "coordinate_space": "output_pixels"}}
    async with SessionLocal() as s:
        assert await deident_service.complete(s, vid, new["token"], ok=True, report=report)
    master = transcode_service.master_key(vid)
    faces = deident_service.coordinates_key(vid)
    assert (_read(master), _read(faces)) == (b"masked", b'{"frames":[1]}')

    _put(old["coordinates_put_url"], b'{"frames":[]}', "application/json")
    _put(old["put_url"], b"stale")
    assert _read(master) == b"masked", "늦은 업로드가 마스킹된 마스터를 덮어썼다"
    assert _read(faces) == b'{"frames":[1]}', "늦은 업로드가 좌표를 덮어썼다"
    async with SessionLocal() as s:
        assert not await deident_service.complete(s, vid, old["token"], ok=True, report=report)
    stale = deident_service.deident_key(vid, old["token"])
    assert storage.object_exists(bucket=_storage.BUCKET, key=stale) is False
    assert _read(master) == b"masked"
