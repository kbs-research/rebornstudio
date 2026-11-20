"""변환 잡의 일시 오류가 이어질 때의 재시도 간격·상한과 잡 URL 유효 기간 테스트.

만료된 URL(403)이나 저장소 장애는 일시 오류로 보고되고 시도를 소모하지 않는다.
상한과 간격이 없으면 다시 돌아도 낫지 않는 일시 오류가 1분마다 끝없이 반복된다.
비식별화(`test_deident_transient`)와 같은 규칙을 확인한다.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.media import transcode as transcode_service
from api_gateway.services.media import video as video_service
from httpx import AsyncClient
from tests._helpers import new_project, upload_video

_EXPIRED = "결과 파일을 올리지 못했습니다 (HTTP 403)"


@pytest.fixture(autouse=True)
async def _isolate():
    """앞 테스트가 남긴 변환·publish·비식별화 활성 행을 비운다."""
    from tests._helpers import reset_transcode_active
    await reset_transcode_active()
    yield


async def _seed(client: AsyncClient, token: str, name: str) -> tuple[str, uuid.UUID]:
    """변환 대기 영상 하나를 만든다."""
    pid = await new_project(client, token, name)
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": {"video_codec": "av1", "height": 1080}}
        v.transcode_status = "pending"
        await s.commit()
    return pid, vid


async def _dispatch(vid: uuid.UUID) -> dict | None:
    """변환 잡을 만들어 이 영상의 잡을 돌려준다. 보내지 않았으면 `None`이다."""
    async with SessionLocal() as s:
        jobs = await transcode_service.build_jobs(s)
    return next((j for j in jobs if j["video_id"] == str(vid)), None)


async def _fail_transient(vid: uuid.UUID, token: str) -> None:
    """만료된 URL로 올리지 못했다는 일시 오류 보고를 넣는다."""
    async with SessionLocal() as s:
        await transcode_service.complete_transcode(
            s, vid, token, ok=False, error=_EXPIRED, transient=True)


async def _wake(vid: uuid.UUID) -> None:
    """재시도 시각을 지난 시각으로 돌린다. 백오프 간격이 지난 상태와 같다."""
    async with SessionLocal() as s:
        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        await video_service.merge_extra(s, vid, {"transcode_retry_after": past})
        await s.commit()


async def test_transient_failure_backs_off_before_the_next_dispatch(
    client: AsyncClient, admin_token: str,
):
    """일시 오류 뒤에는 재시도 시각이 지나야 다시 보낸다. 간격은 연속 횟수에 따라 두 배로 는다."""
    _, vid = await _seed(client, admin_token, "tx-backoff")
    job = await _dispatch(vid)
    await _fail_transient(vid, job["token"])
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "pending" and v.transcode_attempts == 0
        assert v.extra["transcode_transient_streak"] == 1
        first = datetime.fromisoformat(v.extra["transcode_retry_after"])
    assert await _dispatch(vid) is None, "재시도 시각 전에 다시 보냈다"

    await _wake(vid)
    job = await _dispatch(vid)
    assert job is not None
    await _fail_transient(vid, job["token"])
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        second = datetime.fromisoformat(v.extra["transcode_retry_after"])
    assert v.extra["transcode_transient_streak"] == 2
    assert second - datetime.now(UTC) > first - datetime.now(UTC), "간격이 늘지 않았다"


async def test_consecutive_transient_failures_stop_at_the_limit(
    client: AsyncClient, admin_token: str,
):
    """일시 오류가 상한만큼 이어지면 실패로 확정하고, 변환 재시도가 연속 횟수를 지운다."""
    pid, vid = await _seed(client, admin_token, "tx-transient-cap")
    for _ in range(transcode_service._TRANSIENT_LIMIT):
        job = await _dispatch(vid)
        assert job is not None
        await _fail_transient(vid, job["token"])
        await _wake(vid)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "failed"
        assert "일시 오류가 10회 이어져" in v.extra["transcode_error"]
        assert "transcode_transient_streak" not in v.extra
    assert await _dispatch(vid) is None

    r = await client.post(f"/projects/{pid}/videos/{vid}/transcode/retry",
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 204, r.text
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "pending"
        assert "transcode_retry_after" not in v.extra
    assert await _dispatch(vid) is not None


def test_job_urls_outlive_queue_wait_plus_processing():
    """잡 URL은 큐 대기 상한이 지난 뒤 시작한 잡이 입력 길이 상한만큼 처리해도 유효하다."""
    assert (
        transcode_service._QUEUE_WAIT_S + settings.probe_max_duration_seconds) <= transcode_service._JOB_URL_TTL_S
    # presigned URL(SigV4)의 유효 기간 상한은 7일이다.
    assert transcode_service._JOB_URL_TTL_S <= 7 * 24 * 3600
