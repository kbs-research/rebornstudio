"""영상 비트레이트 표시 규칙(`probe.bit_rate`)과 그 값을 싣는 응답을 검증한다.

비트레이트는 판정 항목이 아니라 참고 사실이다.
ffprobe가 읽은 값을 먼저 쓰고, 값을 기록하기 전에 검사한 영상은 용량과 재생 시간으로 평균값을 계산한다.
영상 상세(`metadata.probe`)가 그 값을 보여 주고, 규격 검증 단계 기록에도 읽은 값이 남는다.
"""
from __future__ import annotations

import uuid

from api_gateway.database import SessionLocal
from api_gateway.models import MediaStageRun, Video
from api_gateway.services.media import probe as probe_service
from api_gateway.services.media.probe import bit_rate
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, upload_video

#: 비트레이트를 기록하기 전의 probe 결과. 20초에 20,500,000바이트라서 평균 8.2 Mbps다.
_OLD_META = {"video_codec": "h264", "width": 1280, "height": 720,
             "duration_seconds": 20.0, "size_bytes": 20_500_000,
             "format_name": "mov,mp4,m4a,3gp,3g2,mj2", "fps": 30.0}


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


def test_bit_rate_prefers_measured_value():
    """ffprobe가 읽은 값이 있으면 용량과 재생 시간으로 계산하지 않는다."""
    assert bit_rate({**_OLD_META, "bit_rate": 7_000_000}) == 7_000_000


def test_bit_rate_estimates_from_size_and_duration():
    """측정값이 없으면 용량(바이트)×8÷재생 시간(초)으로 평균값을 계산한다."""
    assert bit_rate(_OLD_META) == 8_200_000


def test_bit_rate_is_none_without_inputs():
    """측정값도 없고 용량이나 재생 시간도 없으면 값을 만들지 않는다."""
    assert bit_rate(None) is None
    assert bit_rate({"size_bytes": 1024}) is None
    assert bit_rate({"duration_seconds": 10.0}) is None
    assert bit_rate({"size_bytes": 1024, "duration_seconds": 0}) is None
    # JSONB에 문자열이나 불리언이 들어와도 숫자로 읽지 않는다.
    assert bit_rate({"bit_rate": "N/A", "size_bytes": True, "duration_seconds": 10.0}) is None


def test_bit_rate_reads_video_when_meta_lacks_size():
    """단계 기록에는 용량이 없으므로 영상의 probe 결과에서 용량을 가져온다."""
    v = Video(extra={"probe": {"size_bytes": 20_500_000}}, file_size_bytes=None,
              duration_seconds=None)
    assert bit_rate({"duration_seconds": 20.0}, v) == 8_200_000
    # probe 결과에 용량이 없으면 영상 열을 쓴다.
    v2 = Video(extra={}, file_size_bytes=10_250_000, duration_seconds=10.0)
    assert bit_rate({}, v2) == 8_200_000


async def _probe(monkeypatch, vid: str, meta: dict) -> None:
    """가짜 ffprobe 결과로 대상 영상 하나만 규격 검사한다. CI에는 ffmpeg가 없다."""
    import reborn_transcode
    monkeypatch.setattr(reborn_transcode, "probe", lambda url, timeout=60: dict(meta))
    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        await probe_service._probe_one(s, v)
        await s.commit()


async def _probe_run_detail(vid: str) -> dict:
    """규격 검증 단계의 최신 기록에 남은 세부 값을 돌려준다."""
    async with SessionLocal() as s:
        run = await s.scalar(
            select(MediaStageRun)
            .where(MediaStageRun.video_id == uuid.UUID(vid), MediaStageRun.stage == "probe")
            .order_by(MediaStageRun.id.desc()).limit(1)
        )
    assert run is not None and run.status == "ok"
    return run.detail or {}


async def test_measured_bit_rate_reaches_record_and_video(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """ffprobe가 읽은 비트레이트를 단계 기록과 영상 메타에 남기고 영상 응답이 그 값을 보여 준다."""
    pid = await new_project(client, admin_token, "br")
    vid = await upload_video(client, admin_token, pid)
    await _probe(monkeypatch, vid, {**_OLD_META, "bit_rate": 7_000_000})

    assert (await _probe_run_detail(vid))["bit_rate"] == 7_000_000
    video = (await client.get(f"/videos/{vid}", headers=_h(admin_token))).json()
    assert video["metadata"]["probe"]["bit_rate"] == 7_000_000


async def test_old_probe_record_gets_average_bit_rate(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """비트레이트를 기록하기 전에 검사한 영상은 영상 응답이 평균값을 보여 준다. 저장된 값은 바꾸지 않는다."""
    pid = await new_project(client, admin_token, "br-old")
    vid = await upload_video(client, admin_token, pid)
    await _probe(monkeypatch, vid, _OLD_META)

    video = (await client.get(f"/videos/{vid}", headers=_h(admin_token))).json()
    assert video["metadata"]["probe"]["bit_rate"] == 8_200_000
    async with SessionLocal() as s:
        row = await s.get(Video, uuid.UUID(vid))
        assert "bit_rate" not in row.extra["probe"], "응답을 만들면서 저장된 probe 결과를 바꾸지 않는다"
