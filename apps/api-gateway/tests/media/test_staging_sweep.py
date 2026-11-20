"""고아 스테이징 정리 테스트. 보고 없이 끝난 시도가 남긴 파일만 지우는지 본다.

워커는 시도마다 `work/{video_id}/{token}/…`에 올린다. 완료 판정이 자기 토큰의 파일을 옮기거나
지우므로 보고 없이 끝난 시도의 파일만 남는다. 정리는 현재 시도의 토큰, 늦은 결과를 이어받을 수
있는 회수 표식의 토큰, 토큰이 없는 마스터 키, 잡 URL 유효 기간 안의 파일, 이 DB에 없는 영상의
파일을 남겨야 한다.

다른 테스트가 같은 버킷을 쓰므로 정리 범위를 이 테스트 영상의 접두사로 좁힌다.
"""
import uuid
from datetime import UTC, datetime, timedelta

from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.media import retention, storage
from api_gateway.services.media import transcode as transcode_service
from httpx import AsyncClient
from tests import _storage
from tests._helpers import new_project, upload_video


def _exists(key: str) -> bool:
    """테스트 버킷에 객체가 있는지 확인한다."""
    return bool(storage.object_exists(bucket=_storage.BUCKET, key=key))


async def test_sweep_deletes_only_orphaned_old_attempts(client: AsyncClient, admin_token: str):
    """현재 토큰·회수 표식 토큰·마스터는 남고, 유효 기간이 지난 낡은 시도의 파일만 지운다."""
    pid = await new_project(client, admin_token, "staging-sweep")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    live, adoptable, orphan = (uuid.uuid4().hex for _ in range(3))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.transcode_status = "running"
        v.transcode_token = live
        v.extra = {**(v.extra or {}), "deident_reclaimed": {"token": adoptable}}
        await s.commit()
    keys = {
        "live": transcode_service.staging_key(vid, live, "analysis-720p.mp4"),
        "adoptable": transcode_service.staging_key(vid, adoptable, "analysis-720p.deid.mp4"),
        "orphan": transcode_service.staging_key(vid, orphan, "play-480p.mp4"),
        "master": transcode_service.master_key(vid),
    }
    for key in keys.values():
        _storage.put_bytes(key, b"x")
    prefix = f"work/{vid}/"

    # 방금 올린 파일은 유효 기간 안이라 낡은 시도의 파일도 남긴다.
    async with SessionLocal() as s:
        assert await retention.sweep_orphan_staging(s, prefix=prefix) == 0
    assert all(_exists(k) for k in keys.values())

    # 유효 기간이 지난 시점에서는 낡은 시도의 파일만 지운다.
    later = datetime.now(UTC) + timedelta(days=2)
    async with SessionLocal() as s:
        assert await retention.sweep_orphan_staging(s, prefix=prefix, now=later) == 1
    assert not _exists(keys["orphan"])
    assert _exists(keys["live"]) and _exists(keys["adoptable"]) and _exists(keys["master"])


async def test_sweep_caps_deletions_per_run(client: AsyncClient, admin_token: str, monkeypatch):
    """한 실행이 지우는 수는 상한을 넘지 않고, 남은 파일은 다음 실행이 지운다."""
    pid = await new_project(client, admin_token, "staging-sweep-cap")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    keys = [transcode_service.staging_key(vid, uuid.uuid4().hex, "analysis-720p.mp4")
            for _ in range(3)]
    for key in keys:
        _storage.put_bytes(key, b"x")
    monkeypatch.setattr(retention, "_SWEEP_MAX_DELETES", 2)
    later = datetime.now(UTC) + timedelta(days=2)
    async with SessionLocal() as s:
        assert await retention.sweep_orphan_staging(s, prefix=f"work/{vid}/", now=later) == 2
    async with SessionLocal() as s:
        assert await retention.sweep_orphan_staging(s, prefix=f"work/{vid}/", now=later) == 1
    assert not any(_exists(k) for k in keys)


async def test_sweep_skips_videos_unknown_to_this_database(client: AsyncClient, admin_token: str):
    """이 DB에 없는 영상 id의 낡은 파일은 남기고, 이 DB 영상의 낡은 시도 파일은 지운다.

    다른 설치본과 버킷을 같이 쓰면 `work/` 아래에 그 설치본의 영상 파일이 있다.
    이 DB가 그 영상의 현재 토큰을 모르므로 토큰 판정만으로는 지울 파일로 고르게 된다.
    soft delete한 영상은 이 DB의 영상이라 정리 대상에 남는다.
    """
    pid = await new_project(client, admin_token, "staging-sweep-foreign")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    gone = uuid.UUID(await upload_video(client, admin_token, pid))
    foreign = uuid.uuid4()
    live, dead, gone_dead, foreign_tok = (uuid.uuid4().hex for _ in range(4))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.transcode_status = "running"
        v.transcode_token = live
        g = await s.get(Video, gone)
        g.deleted_at = datetime.now(UTC)
        await s.commit()
    keys = {
        "live": transcode_service.staging_key(vid, live, "analysis-720p.mp4"),
        "dead": transcode_service.staging_key(vid, dead, "play-480p.mp4"),
        "gone_dead": transcode_service.staging_key(gone, gone_dead, "play-480p.mp4"),
        "foreign": transcode_service.staging_key(foreign, foreign_tok, "x"),
    }
    for key in keys.values():
        _storage.put_bytes(key, b"x")

    later = datetime.now(UTC) + timedelta(days=2)
    async with SessionLocal() as s:
        assert await retention.sweep_orphan_staging(s, prefix=f"work/{foreign}/", now=later) == 0
        assert await retention.sweep_orphan_staging(s, prefix=f"work/{vid}/", now=later) == 1
        assert await retention.sweep_orphan_staging(s, prefix=f"work/{gone}/", now=later) == 1
    assert _exists(keys["foreign"]) and _exists(keys["live"])
    assert not _exists(keys["dead"]) and not _exists(keys["gone_dead"])
