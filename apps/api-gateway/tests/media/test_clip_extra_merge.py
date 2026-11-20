"""클립 JSONB 병합 테스트. 병합이 다른 쓰기의 키를 지우지 않는지 확인한다.

동시 쓰기 상황을 시간 지연 없이 재현한다.
워커가 클립을 읽은 뒤(`clip.extra` 스냅샷) 다른 쓰기가 끼어든다. 그다음 워커가 결과를 기록한다.
병합이 DB에서 일어나면 끼어든 키가 남는다.
썸네일 워커처럼 ffmpeg를 실행하는 수십 초 동안 전체 치환하면 다른 워커가 쓴 키가 지워진다.
영상 쪽은 `video.merge_extra`가 같은 일을 한다.
"""
from uuid import UUID

from httpx import AsyncClient
from tests._helpers import new_project, seed_clip_work, upload_video


async def test_merge_keeps_concurrent_key(client: AsyncClient, admin_token: str):
    """워커가 결과를 써도 그 사이 다른 쓰기가 넣은 키는 남는다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import Clip
    from api_gateway.services.media import clip as clip_service

    pid = await new_project(client, admin_token, "clip-merge")
    vid = await upload_video(client, admin_token, pid)
    cid, _ = await seed_clip_work(pid, vid)

    async with SessionLocal() as s:
        # 워커가 읽은 시점의 스냅샷이다. 이 인스턴스의 extra는 곧 낡은 값이 된다.
        clip = await s.get(Clip, UUID(cid))
        assert (clip.extra or {}) == {}

        # ffmpeg가 도는 동안 다른 쓰기(probe 메타 등)가 끼어든다.
        await clip_service.merge_extra(s, UUID(cid), {"probe": {"fps": 30}})
        await s.commit()

        # 워커가 결과를 기록한다. 이 세션은 낡은 스냅샷을 가지고 있다.
        await clip_service.merge_extra(s, UUID(cid), {"thumbnail_key": "k.jpg"})
        await s.commit()

    async with SessionLocal() as s:
        got = (await s.get(Clip, UUID(cid))).extra or {}
    assert got.get("thumbnail_key") == "k.jpg"
    assert got.get("probe") == {"fps": 30}, f"끼어든 키가 지워졌다 — {got}"


async def test_merge_removes_only_named_keys(client: AsyncClient, admin_token: str):
    """`remove`는 지정한 키만 지운다. 성공한 워커가 실패 기록 키만 지우고 다른 키는 남긴다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import Clip
    from api_gateway.services.media import clip as clip_service

    pid = await new_project(client, admin_token, "clip-merge-rm")
    vid = await upload_video(client, admin_token, pid)
    cid, _ = await seed_clip_work(pid, vid)

    async with SessionLocal() as s:
        await clip_service.merge_extra(
            s, UUID(cid),
            {"thumbnail_error": "boom", "thumbnail_retry": 2, "scene": 7},
        )
        await s.commit()
        await clip_service.merge_extra(
            s, UUID(cid), {"thumbnail_key": "k.jpg"},
            remove=("thumbnail_error", "thumbnail_retry"),
        )
        await s.commit()

    async with SessionLocal() as s:
        got = (await s.get(Clip, UUID(cid))).extra or {}
    assert got == {"scene": 7, "thumbnail_key": "k.jpg"}, got
