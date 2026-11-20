"""파이프라인이 실제 경로에서 켜지는 게이트 지점 테스트.

세 가지 계약을 확인한다.
  1. probe의 코덱 위반은 거부 대신 트랜스코딩을 시작한다. profile=never만 거부한다.
  2. 변환 중(pending/queued/running/failed)인 영상은 dispatch가 가져가지 못한다.
     서버 게이트가 막는다. 화면만 막으면 API로 우회할 수 있다.
  3. 변환이 끝난 영상의 분석 입력은 사본(analysis_uri)이다.
"""
import uuid

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.media import probe as probe_service
from httpx import AsyncClient
from sqlalchemy import update
from tests import _storage
from tests.conftest import uniq


@pytest_asyncio.fixture(autouse=True)
async def _isolate_transcode_rows():
    """변환 활성 행을 격리한다. 근거는 `_helpers.reset_transcode_active`의 docstring에 있다."""
    from tests._helpers import reset_transcode_active
    await reset_transcode_active()
    yield


@pytest_asyncio.fixture(loop_scope="session")
async def fresh_client():
    """dispatch 확인용 FakeMaiuClient를 주입하고 끝나면 싱글턴을 비운다."""
    fc = FakeMaiuClient()
    maiu_client.set_client(fc)
    yield fc
    maiu_client._client_singleton = None


async def _register_one(client: AsyncClient, admin_token: str) -> tuple[str, uuid.UUID]:
    """pre_cleared 영상 1건을 실제 객체와 함께 등록한다. 디스패치 티켓까지 생긴다."""
    slug = f"tg-{uniq('').split('@')[0]}"
    pid = (
        await client.post(
            "/projects", headers={"Authorization": f"Bearer {admin_token}"},
            json={"slug": slug, "name": "tg"},
        )
    ).json()["project_id"]
    await _storage.pin_project(pid)
    key = f"tg/{slug}.mp4"
    _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")
    r = await client.post(
        f"/projects/{pid}/storage/register",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"keys": [key], "copyright_policy": "pre_cleared"},
    )
    return pid, uuid.UUID(r.json()["video_ids"][0])


def _fake_probe(monkeypatch, meta: dict) -> None:
    """ffprobe를 가짜로 바꾼다. probe 판정 규칙만 검증한다(CI에는 ffmpeg가 없다)."""
    import reborn_transcode
    monkeypatch.setattr(reborn_transcode, "probe", lambda url, timeout=60: meta)


async def _run_probe(vid: uuid.UUID) -> None:
    """대상 영상만 직접 probe한다.

    배치(`process_pending`)에는 limit이 있다. 전체 스위트에서는 다른 테스트가 쌓아 둔
    오래된 pending 영상을 먼저 가져가므로 대상 영상이 빠질 수 있다.
    """
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        await probe_service._probe_one(s, v)
        await s.commit()


async def _video_state(vid: uuid.UUID) -> Video:
    """영상 행을 새 세션으로 다시 읽는다."""
    async with SessionLocal() as s:
        return await s.get(Video, vid)


async def test_probe_redeems_codec_violation_into_pipeline(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """av1 영상은 passed로 판정하고 변환을 pending으로 연다. 원래 위반은 감사용으로 남긴다."""
    _fake_probe(monkeypatch, {"video_codec": "av1", "height": 1080, "width": 1920,
                              "duration_seconds": 20.0, "size_bytes": 1024,
                              "format_name": "mp4", "fps": 30.0})
    _, vid = await _register_one(client, admin_token)
    await _run_probe(vid)
    v = await _video_state(vid)
    assert v.probe_status == "passed", "코덱 위반은 이제 거부가 아니라 변환 대상이다"
    assert v.transcode_status == "pending"
    assert v.extra["probe"]["violations"] == []
    assert v.extra["probe"]["transcode_redeemed"] == ["codec_not_allowed (av1)"]


async def test_probe_never_profile_keeps_rejection(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """profile=never면 원본이 그대로 분석에 나가므로 거부한다."""
    _fake_probe(monkeypatch, {"video_codec": "av1", "height": 1080, "width": 1920,
                              "duration_seconds": 20.0, "size_bytes": 1024,
                              "format_name": "mp4", "fps": 30.0})
    _, vid = await _register_one(client, admin_token)
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == vid)
                        .values(transcode_profile="never"))
        await s.commit()
    await _run_probe(vid)
    v = await _video_state(vid)
    assert v.probe_status == "rejected"
    assert v.transcode_status == "skipped"


async def test_probe_h264_within_spec_stays_out_of_pipeline(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """720p h264도 재생본 때문에 파이프라인에 오른다. 480p 이하는 오르지 않는다.

    분석본 기준만 보면 720p h264는 처리하지 않아 480p 재생본이 만들어지지 않는다.
    그러면 라벨러가 원본을 그대로 스트리밍한다. 그래서 판정에 재생본 기준을 더한다.
    """
    # 480p는 분석본도 재생본도 필요 없어서 처리하지 않는다.
    _fake_probe(monkeypatch, {"video_codec": "h264", "height": 480, "width": 854,
                              "duration_seconds": 20.0, "size_bytes": 1024,
                              "format_name": "mp4", "fps": 30.0})
    _, vid0 = await _register_one(client, admin_token)
    await _run_probe(vid0)
    v0 = await _video_state(vid0)
    assert v0.probe_status == "passed" and v0.transcode_status == "skipped"
    assert v0.publish_status == "skipped", "480p는 재생본도 필요 없다"

    # 720p는 분석본이 필요 없지만 재생본(480p)이 필요하므로 파이프라인에 오른다.
    _fake_probe(monkeypatch, {"video_codec": "h264", "height": 720, "width": 1280,
                              "duration_seconds": 20.0, "size_bytes": 1024,
                              "format_name": "mp4", "fps": 30.0})
    _, vid = await _register_one(client, admin_token)
    await _run_probe(vid)
    v = await _video_state(vid)
    assert v.probe_status == "passed"
    # 마스터가 필요 없으면 변환은 돌지 않는다. 재생본은 publish가 원본에서 바로 만든다.
    assert v.transcode_status == "skipped"
    assert v.publish_status == "pending"

    _fake_probe(monkeypatch, {"video_codec": "h264", "height": 1080, "width": 1920,
                              "duration_seconds": 20.0, "size_bytes": 1024,
                              "format_name": "mp4", "fps": 30.0})
    _, vid2 = await _register_one(client, admin_token)
    await _run_probe(vid2)
    v2 = await _video_state(vid2)
    assert v2.probe_status == "passed" and v2.transcode_status == "pending"


async def test_dispatch_gated_while_preparing_then_uses_copy(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """변환 중에는 dispatch가 0건이고(서버 게이트), 변환이 끝나면 사본 URI가 MAIU로 나간다."""
    pid, vid = await _register_one(client, admin_token)
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == vid)
                        .values(probe_status="passed", duration_seconds=60.0,
                                transcode_status="pending"))
        await s.commit()

    r = await client.post(
        f"/projects/{pid}/maiu/dispatch",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"video_ids": [str(vid)]},
    )
    assert r.json()["requested"] == 0, "변환 중 영상이 dispatch를 통과했다 (서버 게이트 구멍)"

    # 변환 완료를 흉내 낸다. analysis_uri가 사본을 가리키게 하고 사본 객체도 넣는다.
    # dispatch 사전 검사(`object_exists`)가 사본의 존재를 확인한다.
    copy_key = f"transcoded/720p/{vid}.mp4"
    _storage.put_bytes(copy_key, b"\x00" * 64, content_type="video/mp4")
    copy_uri = f"s3://{_storage.BUCKET}/{copy_key}"
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == vid)
                        .values(transcode_status="done", analysis_uri=copy_uri))
        await s.commit()
    r = await client.post(
        f"/projects/{pid}/maiu/dispatch",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"video_ids": [str(vid)]},
    )
    assert r.json()["requested"] == 1
    sent = [a["uri"] for a in fresh_client._assets.values()]
    assert copy_uri in sent, "분석 입력이 원본이다 — 사본(analysis_uri)이 나가야 한다"


async def test_bucket_counts_and_list_expose_preparing(
    client: AsyncClient, admin_token: str
):
    """배지와 목록이 '변환 중'을 같은 SQL로 센다. 그래야 두 화면의 수가 같다."""
    pid, vid = await _register_one(client, admin_token)
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == vid)
                        .values(transcode_status="running"))
        await s.commit()
    counts = (await client.get(
        f"/projects/{pid}/videos/buckets",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert counts["preparing"] == 1
    rows = (await client.get(
        f"/projects/{pid}/videos?bucket=preparing",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert [r["video_id"] for r in rows] == [str(vid)]
    assert rows[0]["transcode_status"] == "running"
    assert rows[0]["ingest_bucket"] == "preparing"


# ── 프로파일, 예약, 재생본 ──────────────────────────────

async def test_register_carries_transcode_profile(
    client: AsyncClient, admin_token: str
):
    """등록 페이로드의 변환 프로파일이 컬럼에 저장된다. 등록은 분석을 요청하지 않는다."""
    slug = f"te-{uniq('').split('@')[0]}"
    pid = (
        await client.post(
            "/projects", headers={"Authorization": f"Bearer {admin_token}"},
            json={"slug": slug, "name": "te"},
        )
    ).json()["project_id"]
    await _storage.pin_project(pid)
    key = f"te/{slug}.mp4"
    _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")
    r = await client.post(
        f"/projects/{pid}/storage/register",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"keys": [key], "copyright_policy": "pre_cleared", "transcode_profile": "never"},
    )
    v = await _video_state(uuid.UUID(r.json()["video_ids"][0]))
    assert v.transcode_profile == "never"


async def test_dispatch_waits_for_transcode_and_sends_the_copy(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """변환 중인 영상은 보내지 않고 변환이 끝나면 사본을 보낸다.

    예약 자동 요청은 없으므로 이 두 성질은 요청 경로가 지킨다.
    그래서 수동 요청이 쓰는 코어(`_dispatch_core`)로 확인한다.
    사본 대신 원본을 보내면 h264가 아닌 영상이 MI로 가고 분석과 클립의 시간축이 어긋난다.
    """
    from api_gateway.services.maiu.service import _dispatch_core

    pid, vid = await _register_one(client, admin_token)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.probe_status = "passed"
        v.duration_seconds = v.duration_seconds or 60.0
        v.transcode_status = "pending"   # 아직 변환 중이라 나가면 안 된다
        await s.commit()

    async def _try() -> int:
        """dispatch 코어를 한 번 부르고 새로 만든 요청 수를 돌려준다."""
        async with SessionLocal() as s:
            r = await _dispatch_core(s, pid, [vid], 1, actor_user_id=None)
            return len(r.created)

    assert await _try() == 0, "변환 중인데 요청이 나갔다"

    copy_key = f"transcoded/720p/{vid}.mp4"
    _storage.put_bytes(copy_key, b"\x00" * 64, content_type="video/mp4")
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.transcode_status = "done"
        v.analysis_uri = f"s3://{_storage.BUCKET}/{copy_key}"
        await s.commit()

    assert await _try() == 1
    sent = [a["uri"] for a in fresh_client._assets.values()]
    assert f"s3://{_storage.BUCKET}/{copy_key}" in sent, "요청은 사본이 나가야 한다"


async def test_source_url_prefers_playback_copy(client: AsyncClient, admin_token: str):
    """재생 URL은 480p 재생본을 우선한다. 없으면 원본을 돌려주고 `variant`로 어느 쪽인지 표시한다."""
    from tests._helpers import new_project, upload_video

    pid = await new_project(client, admin_token, "su")
    vid = await upload_video(client, admin_token, pid)

    r = (await client.get(
        f"/videos/{vid}/source-url", headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert r["variant"] == "original"

    play_key = f"transcoded/480p/{vid}.mp4"
    _storage.put_bytes(play_key, b"\x00" * 64, content_type="video/mp4")
    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        v.extra = {**(v.extra or {}), "transcode": {
            "playback_key": play_key, "bucket": _storage.BUCKET,
        }}
        await s.commit()
    r = (await client.get(
        f"/videos/{vid}/source-url", headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert r["variant"] == "playback_480p"
    assert play_key.split("/")[-1] in r["url"]
