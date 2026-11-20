"""비식별화 잡의 일시 오류가 이어지는 동안의 재시도 간격·상한·표시 테스트.

입력을 받지 못하거나 결과를 올리지 못하면 워커는 일시 오류로 보고하고 영상은 대기로 돌아간다. 네 가지를 확인한다.

  1. 일시 오류가 연속될수록 다음 투입까지 간격을 늘린다(백오프). 1분마다 다시 보내지 않는다.
  2. 연속 횟수가 상한에 이르면 실패로 확정한다. 상한이 없으면 재시도가 끝나지 않는다.
  3. 기다리는 동안 준비 타임라인은 `대기`와 사유를 표시한다. `실패`로 표시하지 않는다.
  4. 재시도 요청은 실패로 확정된 뒤에만 받는다. 기다리는 중에는 진행 중이라는 사유로 거절한다.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Project, Video
from api_gateway.services.media import deident as deident_service
from api_gateway.services.media import video as video_service
from api_gateway.services.media.pipeline import open_next
from httpx import AsyncClient
from tests._helpers import new_project, upload_video

_UNREACHABLE = "입력 영상을 받는 중 연결이 끊겼습니다"


@pytest.fixture(autouse=True)
async def _isolate():
    """앞 테스트가 남긴 변환·publish·비식별화 활성 행을 비운다."""
    from tests._helpers import reset_transcode_active
    await reset_transcode_active()
    yield


async def _seed(client: AsyncClient, token: str, name: str) -> tuple[str, uuid.UUID]:
    """비식별화를 켠 프로젝트에 720p 영상을 올리고 비식별화 대기까지 연다."""
    pid = await new_project(client, token, name)
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        p = await s.get(Project, uuid.UUID(pid))
        p.deident_enabled = True
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": {
            "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"}}
        await s.commit()
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        await open_next(s, v)
        await s.commit()
    return pid, vid


async def _dispatch(vid: uuid.UUID) -> dict | None:
    """비식별화 잡을 만들어 이 영상의 잡을 돌려준다. 보내지 않았으면 `None`이다."""
    async with SessionLocal() as s:
        jobs = await deident_service.build_jobs(s)
    return next((j for j in jobs if j["video_id"] == str(vid)), None)


async def _fail_transient(vid: uuid.UUID, token: str) -> None:
    """연결이 끊겼다는 일시 오류 보고를 넣는다."""
    async with SessionLocal() as s:
        await deident_service.complete(s, vid, token, ok=False, error=_UNREACHABLE, transient=True)


async def _wake(vid: uuid.UUID) -> None:
    """재시도 시각을 지난 시각으로 돌린다. 백오프 간격이 지난 상태와 같다."""
    async with SessionLocal() as s:
        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        await video_service.merge_extra(s, vid, {"deident_retry_after": past})
        await s.commit()


async def _entry(client: AsyncClient, token: str, vid: uuid.UUID) -> dict:
    """준비 타임라인에서 비식별화 단계 줄을 읽는다."""
    r = await client.get(f"/videos/{vid}/prep-timeline",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["entries"] if x["stage"] == "deident")


async def test_transient_failure_backs_off_before_the_next_dispatch(
    client: AsyncClient, admin_token: str,
):
    """일시 오류 뒤에는 간격이 지나야 다시 보낸다. 간격은 연속될수록 길어진다."""
    _, vid = await _seed(client, admin_token, "deid-backoff")
    job = await _dispatch(vid)
    await _fail_transient(vid, job["token"])
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "pending"
        assert v.deident_attempts == 0, "일시 오류는 시도를 소모하지 않는다"
        assert v.extra["deident_transient_streak"] == 1
        first = datetime.fromisoformat(v.extra["deident_retry_after"]) - datetime.now(UTC)
    assert timedelta(seconds=30) < first <= timedelta(seconds=60)
    assert await _dispatch(vid) is None, "쉬는 동안 다시 보냈다"

    await _wake(vid)
    job = await _dispatch(vid)
    assert job is not None, "간격이 지났는데 보내지 않았다"
    await _fail_transient(vid, job["token"])
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.extra["deident_transient_streak"] == 2
        second = datetime.fromisoformat(v.extra["deident_retry_after"]) - datetime.now(UTC)
    assert second > first + timedelta(seconds=30), "이어졌는데 간격이 늘지 않았다"


async def test_transient_streak_has_a_cap(client: AsyncClient, admin_token: str):
    """일시 오류가 상한만큼 연속되면 실패로 확정한다. 사유에 자동 재시도를 멈춘 이유를 남긴다."""
    _, vid = await _seed(client, admin_token, "deid-transient-cap")
    async with SessionLocal() as s:
        await video_service.merge_extra(
            s, vid, {"deident_transient_streak": deident_service._TRANSIENT_LIMIT - 1})
        await s.commit()
    job = await _dispatch(vid)
    await _fail_transient(vid, job["token"])
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "failed"
        assert "자동 재시도를 멈췄습니다" in v.extra["deident_error"]
        assert "[일시]" not in v.extra["deident_error"]
        assert "deident_retry_after" not in v.extra


async def test_timeline_shows_waiting_not_failed_while_retrying(
    client: AsyncClient, admin_token: str,
):
    """자동 재시도를 기다리는 동안 타임라인은 `대기`와 사유를 표시한다. 확정 실패만 `실패`로 표시한다."""
    pid, vid = await _seed(client, admin_token, "deid-timeline-wait")
    job = await _dispatch(vid)
    await _fail_transient(vid, job["token"])

    e = await _entry(client, admin_token, vid)
    assert e["status"] == "pending", e
    assert e["note"].startswith("재시도 중"), e
    assert _UNREACHABLE in e["note"]
    assert e["error"] is None, "기다리는 줄에 실패 사유를 펼치면 실패로 읽힌다"

    # 기다리는 중에 누른 재시도는 진행 중이라는 사유로 거절한다.
    r = await client.post(f"/projects/{pid}/videos/{vid}/deident/retry",
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 409 and r.json()["detail"] == "deident_in_progress", r.text

    # 확정 실패면 실패로 표시하고 재시도를 받는다.
    async with SessionLocal() as s:
        await video_service.merge_extra(
            s, vid, {"deident_transient_streak": deident_service._TRANSIENT_LIMIT - 1})
        await s.commit()
    await _wake(vid)
    job = await _dispatch(vid)
    await _fail_transient(vid, job["token"])
    e = await _entry(client, admin_token, vid)
    assert e["status"] == "failed", e
    r = await client.post(f"/projects/{pid}/videos/{vid}/deident/retry",
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 204, r.text
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "pending"
        assert "deident_transient_streak" not in v.extra, "다시 넣었는데 앞선 연속이 남았다"


async def test_success_clears_the_transient_streak(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """성공하면 일시 오류 연속 횟수와 재시도 시각을 지운다. 다음 실패는 처음부터 센다."""
    monkeypatch.setattr("api_gateway.services.media.deident.storage.copy_object",
                        lambda **kw: None)
    monkeypatch.setattr("api_gateway.services.media.deident.storage.delete_object",
                        lambda **kw: None)
    _, vid = await _seed(client, admin_token, "deid-transient-clear")
    job = await _dispatch(vid)
    await _fail_transient(vid, job["token"])
    await _wake(vid)
    job = await _dispatch(vid)
    async with SessionLocal() as s:
        assert await deident_service.complete(
            s, vid, job["token"], ok=True, elapsed_s=1.0,
            report={"model": "face-anonymizer", "masked": True}) is True
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert "deident_transient_streak" not in v.extra
        assert "deident_retry_after" not in v.extra
