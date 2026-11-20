"""규격 검사(probe)의 일시 오류 재시도와 단일 실행 테스트(`services/media/probe`)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.media import probe as probe_service
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, upload_video


def _raising_probe(monkeypatch, exc: Exception) -> list[str]:
    """ffprobe를 정해진 예외를 내는 가짜로 바꾸고 호출된 URL 목록을 돌려준다."""
    import reborn_transcode

    calls: list[str] = []

    def _probe(url, timeout=60):
        """호출을 기록하고 예외를 낸다."""
        calls.append(url)
        raise exc

    monkeypatch.setattr(reborn_transcode, "probe", _probe)
    return calls


async def _probe_once(vid: uuid.UUID) -> bool:
    """대상 영상 한 건만 probe하고 상태를 바꿨는지 돌려준다."""
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        changed = await probe_service._probe_one(s, v)
        await s.commit()
        return changed


async def _video(vid: uuid.UUID) -> Video:
    """영상 행을 새 세션으로 다시 읽는다."""
    async with SessionLocal() as s:
        return await s.get(Video, vid)


async def _new_video(client: AsyncClient, admin_token: str) -> uuid.UUID:
    """업로드를 마친 probe 대기 영상 1건을 만든다."""
    pid = await new_project(client, admin_token, "prb")
    return uuid.UUID(await upload_video(client, admin_token, pid))


@pytest.mark.parametrize("make_exc", [
    lambda m: m.ProbeError("ffprobe failed (rc=1): Connection timed out"),
    lambda m: m.ProbeError("ffprobe failed (rc=1): Server returned 503 Service Unavailable"),
    lambda m: m.ProbeTimeoutError("ffprobe timeout after 60s"),
])
async def test_transient_probe_failure_stays_pending(
    client: AsyncClient, admin_token: str, monkeypatch, make_exc,
):
    """망 순단·5xx·제한 시간 초과는 `error`로 확정하지 않고 대기로 되돌린다.

    `error`는 운영자가 재검사하기 전까지 분석 요청을 막는다. 순단 한 번으로 영상이 멈추지 않게 한다.
    """
    import reborn_transcode

    vid = await _new_video(client, admin_token)
    _raising_probe(monkeypatch, make_exc(reborn_transcode))
    assert await _probe_once(vid) is False
    v = await _video(vid)
    assert v.probe_status == "pending"
    assert v.extra.get(probe_service._STREAK_KEY) == 1
    assert v.extra.get(probe_service._RETRY_AFTER_KEY)


async def test_transient_probe_failure_has_a_cap(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """일시 오류가 상한만큼 이어지면 `error`로 확정하고 재시도 기록을 지운다."""
    import reborn_transcode

    vid = await _new_video(client, admin_token)
    _raising_probe(monkeypatch, reborn_transcode.ProbeError("rc=1: Connection reset by peer"))
    for _ in range(probe_service._MAX_TRANSIENT):
        await _probe_once(vid)
        assert (await _video(vid)).probe_status == "pending"
    assert await _probe_once(vid) is True
    v = await _video(vid)
    assert v.probe_status == "error"
    assert probe_service._STREAK_KEY not in v.extra
    assert probe_service._RETRY_AFTER_KEY not in v.extra


async def test_broken_file_is_error_at_once(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """깨진 파일처럼 일시 오류가 아닌 실패는 바로 `error`로 확정한다."""
    import reborn_transcode

    vid = await _new_video(client, admin_token)
    _raising_probe(monkeypatch, reborn_transcode.ProbeError(
        "ffprobe failed (rc=1): Invalid data found when processing input"))
    assert await _probe_once(vid) is True
    assert (await _video(vid)).probe_status == "error"


async def test_waiting_video_is_not_a_candidate(client: AsyncClient, admin_token: str):
    """재시도 시각이 지나지 않은 영상은 배치 후보에서 빠지고, 지나면 다시 들어온다."""
    from api_gateway.services.media.video import merge_extra

    vid = await _new_video(client, admin_token)

    async def _is_candidate() -> bool:
        """대상 영상이 지금 배치 후보이면 참이다."""
        async with SessionLocal() as s:
            return await s.scalar(select(Video.video_id).where(
                *probe_service._pending_conditions(), Video.video_id == vid)) is not None

    assert await _is_candidate()
    async with SessionLocal() as s:
        later = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        await merge_extra(s, vid, {probe_service._RETRY_AFTER_KEY: later})
        await s.commit()
    assert not await _is_candidate()
    async with SessionLocal() as s:
        earlier = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        await merge_extra(s, vid, {probe_service._RETRY_AFTER_KEY: earlier})
        await s.commit()
    assert await _is_candidate()


async def test_overlapping_batch_does_not_run(monkeypatch):
    """다른 실행이 배치를 돌리는 동안 겹친 실행은 아무 영상도 검사하지 않는다.

    두 실행이 같은 대기 영상을 함께 검사하면 단계 기록과 감사 이벤트가 두 번 남는다.
    """
    import reborn_transcode
    from api_gateway.services.joblock import single_runner

    calls = _raising_probe(monkeypatch, reborn_transcode.ProbeError("호출되면 안 된다"))
    async with single_runner(probe_service._LOCK_KEY) as got:
        assert got
        async with SessionLocal() as s:
            assert await probe_service.process_pending(s) == 0
    assert calls == []
