"""브라우저로 올린 원본도 MI 분석 요청이 형식 오류 없이 나가는지 확인한다.

업로드 원본은 확장자 없는 키(`intake/{id}/source`)에 있다. 원본이 이미 분석 규격이면 변환을
건너뛰어 분석본이 없다. 그러면 이 키가 분석 입력이 된다.
MI는 키 확장자로 형식을 판별하므로 확장자 없는 키를 거절한다(10022).
그래서 이미 올라간 원본도 분석 요청 때 키에 확장자를 붙여 보낸다.
가짜 MI도 같은 규칙으로 거절하므로 이 테스트는 실제 MI와 같은 조건에서 돈다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.media import storage
from httpx import AsyncClient
from tests import _storage
from tests._helpers import new_project, upload_video


@pytest_asyncio.fixture(loop_scope="session")
async def fake():
    """가짜 MI를 넣고 끝나면 싱글턴을 비운다."""
    c = FakeMaiuClient()
    maiu_client.set_client(c)
    yield c
    maiu_client._client_singleton = None


def _h(token: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _video(video_id: str) -> Video:
    """영상 행을 새 세션으로 읽는다."""
    async with SessionLocal() as s:
        return await s.get(Video, UUID(video_id))


async def _skip_transcode(video_id: str, *, format_name: str = "mov,mp4,m4a,3gp,3g2,mj2") -> None:
    """원본이 분석 규격이라 변환을 건너뛰고 재생본까지 끝난 상태로 만든다."""
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(video_id))
        v.transcode_status = "skipped"
        v.deident_status = "skipped"
        v.publish_status = "done"
        v.extra = {**(v.extra or {}), "probe": {"format_name": format_name}}
        await s.commit()


async def _dispatch(client: AsyncClient, token: str, pid: str, vid: str) -> dict:
    """영상 하나를 골라 분석 요청하고 응답 본문을 돌려준다."""
    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=_h(token),
                          json={"video_ids": [vid]})
    assert r.status_code == 200, r.text
    return r.json()


async def test_uploaded_source_gets_an_extension_key(
    client: AsyncClient, admin_token: str, fake
):
    """확장자 없는 업로드 원본을 같은 경로의 .mp4 키로 옮겨 보낸다. 분석본은 만들지 않는다.

    분석본이 있으면 납품이 시작 시각을 보정한다. 그래서 바이트가 같은 사본을 분석본으로 두지 않는다.
    확장자 없는 원래 키는 커밋 뒤에 지운다.
    """
    pid = await new_project(client, admin_token, "ext-copy")
    vid = await upload_video(client, admin_token, pid, probed=True)
    await _skip_transcode(vid)
    async with SessionLocal() as s:   # 지난 요청이 남긴 거절 사유
        v = await s.get(Video, UUID(vid))
        v.extra = {**v.extra, "dispatch_error": "분석 서비스가 요청을 거절했습니다 (코드 10022)"}
        await s.commit()
    assert (await _video(vid)).source_uri.endswith(f"intake/{vid}/source")

    body = await _dispatch(client, admin_token, pid, vid)
    assert body["failed"] == 0, (await _video(vid)).extra.get("dispatch_error")
    assert len(body["created"]) == 1

    want = f"s3://{_storage.BUCKET}/intake/{vid}/source.mp4"
    assert want in [a["uri"] for a in fake._assets.values()]
    v = await _video(vid)
    assert v.source_uri == want
    assert v.analysis_uri is None, "바이트가 같은 사본을 분석본으로 두면 납품 시각이 보정된다"
    assert storage.object_exists(bucket=_storage.BUCKET, key=f"intake/{vid}/source.mp4")
    assert storage.object_exists(bucket=_storage.BUCKET, key=f"intake/{vid}/source") is False
    assert v.extra["source_renamed"]["from"] == f"intake/{vid}/source"
    assert "dispatch_error" not in v.extra, "나간 영상에 지난 거절 사유가 남았다"


async def test_matroska_source_keeps_its_container_extension(
    client: AsyncClient, admin_token: str, fake
):
    """원본 컨테이너가 matroska면 새 키에 .mkv를 붙인다. 내용과 다른 확장자를 붙이지 않는다."""
    pid = await new_project(client, admin_token, "ext-mkv")
    vid = await upload_video(client, admin_token, pid, probed=True)
    await _skip_transcode(vid, format_name="matroska,webm")

    body = await _dispatch(client, admin_token, pid, vid)
    assert len(body["created"]) == 1
    assert (await _video(vid)).source_uri.endswith(f"intake/{vid}/source.mkv")


async def test_key_with_supported_extension_is_sent_as_is(
    client: AsyncClient, admin_token: str, fake
):
    """확장자가 이미 맞는 키(저장소 등록 영상)는 이름을 바꾸지 않고 그대로 보낸다."""
    pid = await new_project(client, admin_token, "ext-keep")
    vid = await upload_video(client, admin_token, pid, probed=True)
    await _skip_transcode(vid)
    key = f"library/{vid}.mov"
    _storage.put_bytes(key, b"\x00" * 64, content_type="video/quicktime")
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.source_uri = f"s3://{_storage.BUCKET}/{key}"
        await s.commit()

    body = await _dispatch(client, admin_token, pid, vid)
    assert len(body["created"]) == 1
    assert f"s3://{_storage.BUCKET}/{key}" in [a["uri"] for a in fake._assets.values()]
    v = await _video(vid)
    assert v.source_uri == f"s3://{_storage.BUCKET}/{key}" and v.analysis_uri is None
