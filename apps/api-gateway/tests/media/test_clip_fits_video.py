"""클립 끝이 영상 길이를 넘지 않는지 확인하는 테스트.

분석기가 영상보다 긴 구간을 돌려주면 클립이 없는 프레임을 가리키고 납품 분량(초)이 실제보다 커진다.
저장 직전의 `create_scene_clip`이 끝을 영상 끝으로 줄이고, 남는 구간이 없거나 하한보다 짧으면 만들지 않는다.
영상 길이를 모르면 구간을 그대로 만든다.
"""
import uuid
from decimal import Decimal

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Clip, Video
from api_gateway.services.media import clip as clip_service
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, upload_video


@pytest.mark.parametrize(
    ("start", "end", "duration", "expected"),
    [
        (30.0, 60.0, 40.0, (30.0, 40.0)),
        (0.0, 30.0, 40.0, (0.0, 30.0)),
        (45.0, 60.0, 40.0, None),
        (40.0, 60.0, 40.0, None),
        (30.0, 60.0, None, (30.0, 60.0)),
        (30.0, 60.0, 0.0, (30.0, 60.0)),
        (30.0, 41.0, 40.0336, (30.0, 40.033)),
    ],
)
def test_fit_to_video(start, end, duration, expected):
    """끝만 영상 끝으로 줄이고, 시작이 영상 끝 이후면 버리고, 길이를 모르면 그대로 둔다."""
    assert clip_service.fit_to_video(start, end, duration) == expected


async def _video(client: AsyncClient, token: str, duration: float | None) -> uuid.UUID:
    """테스트용 프로젝트와 영상을 만들고 영상 길이를 `duration`으로 둔다."""
    pid = await new_project(client, token, f"fit-{uuid.uuid4().hex[:6]}")
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.duration_seconds = duration
        await s.commit()
    return vid


async def _make(video_id: uuid.UUID, start: float, end: float) -> bool:
    """새 세션에서 씬 클립 한 건을 만든다."""
    async with SessionLocal() as s:
        v = await s.get(Video, video_id)
        made = await clip_service.create_scene_clip(
            s, video=v, start_seconds=start, end_seconds=end,
            auto_caption={}, speakers=[], scene_extra={"source": "test"},
        )
        await s.commit()
        return made


async def _ranges(video_id: uuid.UUID) -> list[tuple[Decimal, Decimal]]:
    """영상의 클립 구간을 시작 순으로 읽는다."""
    async with SessionLocal() as s:
        rows = (await s.scalars(
            select(Clip).where(Clip.video_id == video_id).order_by(Clip.start_seconds)
        )).all()
        return [(c.start_seconds, c.end_seconds) for c in rows]


async def test_clip_past_the_end_is_clamped(client: AsyncClient, admin_token: str):
    """끝이 영상 길이를 넘는 클립은 영상 끝까지로 줄여 만든다."""
    vid = await _video(client, admin_token, 40.0)
    assert await _make(vid, 0.0, 30.0)
    assert await _make(vid, 30.0, 60.0)
    assert await _ranges(vid) == [
        (Decimal("0.000"), Decimal("30.000")), (Decimal("30.000"), Decimal("40.000")),
    ]


async def test_clip_wholly_past_the_end_is_skipped(client: AsyncClient, admin_token: str):
    """시작부터 영상 끝 이후인 클립은 만들지 않는다."""
    vid = await _video(client, admin_token, 40.0)
    assert not await _make(vid, 45.0, 60.0)
    assert await _ranges(vid) == []


async def test_clamped_clip_below_the_floor_is_skipped(client: AsyncClient, admin_token: str):
    """줄인 구간이 클립 길이 하한(기본 10초)보다 짧으면 만들지 않는다."""
    vid = await _video(client, admin_token, 35.0)
    assert not await _make(vid, 30.0, 60.0)
    assert await _ranges(vid) == []


async def test_short_clip_inside_the_video_is_kept(client: AsyncClient, admin_token: str):
    """영상 안의 짧은 구간은 하한과 관계없이 그대로 만든다. 하한은 줄인 구간에만 적용한다."""
    vid = await _video(client, admin_token, 40.0)
    assert await _make(vid, 0.0, 3.0)
    assert await _ranges(vid) == [(Decimal("0.000"), Decimal("3.000"))]


async def test_unknown_duration_keeps_the_range(client: AsyncClient, admin_token: str):
    """영상 길이를 모르면 구간을 줄이지 않는다."""
    vid = await _video(client, admin_token, None)
    assert await _make(vid, 30.0, 60.0)
    assert await _ranges(vid) == [(Decimal("30.000"), Decimal("60.000"))]
