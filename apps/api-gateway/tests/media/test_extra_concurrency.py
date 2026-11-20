"""`videos.metadata`(JSONB)에 동시에 써도 키가 사라지지 않는지 확인하는 테스트.

오래된 ORM 인스턴스를 가진 쪽이 나중에 써도 그 사이 다른 세션이 넣은 키가 남아야 한다.
썸네일 워커가 ffmpeg를 실행하는 동안 probe가 `probe` 키를 쓴다. 썸네일 워커가 오래된 스냅샷으로
전체를 치환하면 그 키가 지워지고, 변환 사유 판정(`needs_transcode`)이 근거 값을 잃는다.
"""
import uuid

from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.media import probe as probe_service
from api_gateway.services.media import video as video_service
from api_gateway.services.media import video_thumbnail
from httpx import AsyncClient
from tests._helpers import new_project, upload_video


async def test_merge_extra_survives_stale_writer(client: AsyncClient, admin_token: str):
    """A가 행을 읽어 둔 사이 B가 키를 넣는다. A가 나중에 써도 B의 키가 남는다."""
    pid = await new_project(client, admin_token, "race")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))

    # A는 오래된 스냅샷을 가진 워커다. 썸네일 배치가 ffmpeg를 실행하는 동안의 상태다.
    async with SessionLocal() as sa:
        stale = await sa.get(Video, vid)
        snapshot = dict(stale.extra or {})   # 이 시점의 스냅샷을 읽어 둔다
        assert snapshot, "업로드가 남긴 키가 있어야 보존 여부를 확인할 수 있다"

        # B는 그 사이 다른 세션에서 probe 메타를 넣고 커밋한다.
        async with SessionLocal() as sb:
            fresh = await sb.get(Video, vid)
            await probe_service._apply(
                sb, fresh, status="passed",
                detail={"video_codec": "av1", "height": 1080, "violations": []},
            )
            await sb.commit()

        # A가 나중에 자기 키를 쓴다.
        await video_service.merge_extra(sa, vid, {"poster_thumbnail_key": "p/x.jpg"})
        await sa.commit()

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.extra.get("poster_thumbnail_key") == "p/x.jpg", "A의 쓰기가 반영돼야 한다"
        assert v.extra.get("probe", {}).get("video_codec") == "av1", (
            "B의 probe 메타가 사라졌다 — lost update 재발"
        )
        # 업로드가 남긴 키(저장소 위치·ETag)도 두 쓰기 뒤에 같은 값으로 남아야 한다.
        assert {k: v.extra.get(k) for k in snapshot} == snapshot


async def test_thumbnail_success_does_not_erase_probe(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """썸네일 성공 경로가 probe 키를 보존한다. 전체 치환으로 키를 지우기 쉬운 경로다."""
    pid = await new_project(client, admin_token, "race2")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        await probe_service._apply(
            s, v, status="passed",
            detail={"video_codec": "h264", "height": 1080, "violations": []},
        )
        await s.commit()

    # 프레임 추출·업로드는 가짜다. 검증 대상은 extra 병합이다.
    monkeypatch.setattr(video_thumbnail, "_POSTER_TS", 1)
    import api_gateway.services.media.clip_thumbnail as ct
    monkeypatch.setattr(ct, "_extract_frame", lambda url, ts: b"\xff\xd8jpeg")

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert await video_thumbnail._process_one(s, v) is True

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.extra["poster_thumbnail_key"].endswith(".jpg")
        assert v.extra["probe"]["video_codec"] == "h264", "썸네일이 probe를 지웠다"


async def test_merge_extra_remove_keys(client: AsyncClient, admin_token: str):
    """`remove`는 병합과 함께 원자적으로 적용된다. 성공한 워커가 실패 기록만 지우고 나머지 키는 남긴다."""
    pid = await new_project(client, admin_token, "race3")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        await video_service.merge_extra(
            s, vid, {"transcode_error": "[일시] boom", "probe": {"video_codec": "av1"}})
        await s.commit()
    async with SessionLocal() as s:
        await video_service.merge_extra(
            s, vid, {"transcode": {"ok": True}}, remove=("transcode_error",))
        await s.commit()
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert "transcode_error" not in v.extra
        assert v.extra["transcode"] == {"ok": True}
        assert v.extra["probe"]["video_codec"] == "av1"
