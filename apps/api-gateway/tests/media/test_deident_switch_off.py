"""비식별화를 끈 프로젝트의 대기 영상 처리 테스트.

자동 재시도를 기다리던 영상은 프로젝트에서 비식별화를 끈 뒤 비식별화 워커로 보내지 않고 재생본 단계로 넘긴다.
넘기지 않으면 영상이 "변환 중"에 멈춘다. 네 가지를 확인한다.

  1. 끈 프로젝트의 대기 영상은 비식별화 워커로 보내지 않는다.
  2. 그 영상은 비식별화 `skipped`로 넘어가고 다음 단계(재생본)가 열린다. 감사 기록이 남는다.
  3. 넘어간 영상은 영상 여정·분석 게이트가 "쓰지 않음"으로 판정한다(`deident_gap`·공용 준비 판정).
  4. 처리 중인 영상은 건드리지 않는다. 일시 오류로 돌아오면 그때 넘긴다. 끈 프로젝트의 실패는 다시 넣지 않는다.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent, MediaStageRun, Project, Video
from api_gateway.services.deident_gap import DEIDENT_OFF
from api_gateway.services.media import deident as deident_service
from api_gateway.services.media import stage_runs
from api_gateway.services.media import video as video_service
from api_gateway.services.media.pipeline import open_next
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, upload_video

_UNREACHABLE = "입력 영상을 받는 중 연결이 끊겼습니다"


@pytest.fixture(autouse=True)
async def _isolate():
    """앞 테스트가 남긴 변환·publish·비식별화 활성 행을 비운다."""
    from tests._helpers import reset_transcode_active
    await reset_transcode_active()
    yield


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _seed(client: AsyncClient, token: str, name: str) -> tuple[str, uuid.UUID]:
    """비식별화를 켠 프로젝트에 720p 영상을 올리고 비식별화 대기까지 연다."""
    pid = await new_project(client, token, name)
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        p = await s.get(Project, uuid.UUID(pid))
        p.deident_enabled = True
        v = await s.get(Video, vid)
        v.probe_status = "passed"
        v.extra = {**(v.extra or {}), "probe": {
            "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"}}
        await s.commit()
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert await open_next(s, v) == "deident"
        await s.commit()
    return pid, vid


async def _dispatch(vid: uuid.UUID) -> dict | None:
    """비식별화 잡을 만들어 이 영상의 잡을 돌려준다. 보내지 않았으면 `None`이다."""
    async with SessionLocal() as s:
        jobs = await deident_service.build_jobs(s)
    return next((j for j in jobs if j["video_id"] == str(vid)), None)


async def _fail_transient(vid: uuid.UUID, token: str) -> None:
    """연결이 끊겼다는 일시 오류 보고를 넣는다. 영상은 재시도 시각이 붙은 대기 상태로 돌아간다."""
    async with SessionLocal() as s:
        await deident_service.complete(s, vid, token, ok=False, error=_UNREACHABLE, transient=True)


async def _switch_off(client: AsyncClient, token: str, pid: str) -> None:
    """프로젝트 설정 화면과 같은 경로로 비식별화를 끈다."""
    r = await client.patch(f"/projects/{pid}", headers=_h(token), json={"deident_enabled": False})
    assert r.status_code == 200, r.text


async def _entry(client: AsyncClient, token: str, vid: uuid.UUID, stage: str) -> dict:
    """준비 타임라인에서 그 단계 줄을 읽는다."""
    r = await client.get(f"/videos/{vid}/prep-timeline", headers=_h(token))
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["entries"] if x["stage"] == stage)


async def test_waiting_video_moves_on_without_deident_after_switch_off(
    client: AsyncClient, admin_token: str,
):
    """재시도를 기다리던 영상은 끈 뒤 서버로 보내지 않고 건너뜀으로 넘어가 재생본 단계가 열린다."""
    pid, vid = await _seed(client, admin_token, "deid-off-waiting")
    job = await _dispatch(vid)
    await _fail_transient(vid, job["token"])
    await _switch_off(client, admin_token, pid)
    # 재시도 시각이 지난 상태로 만든다. 켜진 프로젝트라면 이때 같은 서버로 다시 보낸다.
    async with SessionLocal() as s:
        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        await video_service.merge_extra(s, vid, {"deident_retry_after": past})
        await s.commit()

    assert await _dispatch(vid) is None, "끈 프로젝트의 영상을 비식별화 워커로 보냈다"
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "skipped"
        assert v.deident_token is None
        assert v.publish_status == "pending", "재생본 단계가 열리지 않았다"
        for key in ("deident_error", "deident_transient_streak", "deident_retry_after"):
            assert key not in v.extra, key
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "video.deident_skipped",
            DomainEvent.target_id == vid))).all()
    assert len(ev) == 1, "건너뛴 사실이 감사 기록에 없다"
    assert str(ev[0].project_id) == pid
    assert ev[0].payload["reason"] == "project_disabled"
    assert _UNREACHABLE in ev[0].payload["previous_error"]

    e = await _entry(client, admin_token, vid, "deident")
    assert e["status"] == "skipped" and e["skipped_reason"] == DEIDENT_OFF, e
    assert e["error"] is None, "쓰지 않기로 한 단계를 실패로 그렸다"
    async with SessionLocal() as s:
        runs = (await s.scalars(select(MediaStageRun).where(
            MediaStageRun.video_id == vid, MediaStageRun.stage == "deident"))).all()
    assert len(runs) == 1, "넘기기 전의 시도가 이력에서 사라졌다"
    assert await _dispatch(vid) is None, "두 번째 주기에 다시 보냈다"


async def test_skipped_video_is_not_held_by_the_analysis_gate(
    client: AsyncClient, admin_token: str,
):
    """넘어간 영상은 공용 준비 판정이 "가리지 않음"으로 막지 않는다. 재생본만 끝나면 분석 게이트를 통과한다."""
    from api_gateway.services.media.video import _analysis_hold_case

    pid, vid = await _seed(client, admin_token, "deid-off-gate")
    job = await _dispatch(vid)
    await _fail_transient(vid, job["token"])
    await _switch_off(client, admin_token, pid)
    await _dispatch(vid)
    async with SessionLocal() as s:
        await s.execute(Video.__table__.update().where(Video.video_id == vid)
                        .values(publish_status="done", duration_seconds=25.0))
        await s.commit()
        hold = await s.scalar(select(_analysis_hold_case()).where(Video.video_id == vid))
    assert hold is None, hold


async def test_running_video_is_left_to_finish_and_skipped_if_it_comes_back(
    client: AsyncClient, admin_token: str,
):
    """처리 중인 영상은 끈 순간 건드리지 않는다. 일시 오류로 돌아오면 다음 주기에 넘긴다."""
    pid, vid = await _seed(client, admin_token, "deid-off-running")
    job = await _dispatch(vid)
    await _switch_off(client, admin_token, pid)
    await _dispatch(vid)
    async with SessionLocal() as s:
        assert (await s.get(Video, vid)).deident_status == "running"
    await _fail_transient(vid, job["token"])
    assert await _dispatch(vid) is None
    async with SessionLocal() as s:
        assert (await s.get(Video, vid)).deident_status == "skipped"


async def test_failed_video_of_an_off_project_is_not_requeued(
    client: AsyncClient, admin_token: str,
):
    """끈 프로젝트의 실패한 영상은 재시도를 409로 거절한다. 다시 넣어도 서버로 보내지 않는다."""
    pid, vid = await _seed(client, admin_token, "deid-off-retry")
    async with SessionLocal() as s:
        await s.execute(Video.__table__.update().where(Video.video_id == vid)
                        .values(deident_status="failed"))
        await s.commit()
    await _switch_off(client, admin_token, pid)
    r = await client.post(f"/projects/{pid}/videos/{vid}/deident/retry", headers=_h(admin_token))
    assert r.status_code == 409 and r.json()["detail"] == "deident_not_retryable", r.text


async def test_skip_record_counts_the_attempts_actually_sent(
    client: AsyncClient, admin_token: str,
):
    """건너뜀 감사 기록의 시도 수를 단계 이력에 남은 실제 전송 횟수로 기록한다.

    일시 오류는 상한에 세지 않으려고 `deident_attempts`를 되돌린다. 그 값을 시도 수로 적으면
    서버에 두 번 보낸 영상이 "시도 0"으로 남는다. 상한에 센 수는 `counted_attempts`로 따로 싣는다.
    """
    pid, vid = await _seed(client, admin_token, "deid-off-attempts")
    for _ in range(2):
        job = await _dispatch(vid)
        await _fail_transient(vid, job["token"])
        async with SessionLocal() as s:
            past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            await video_service.merge_extra(s, vid, {"deident_retry_after": past})
            await s.commit()
    await _switch_off(client, admin_token, pid)
    assert await _dispatch(vid) is None

    async with SessionLocal() as s:
        sent = (await stage_runs.attempt_counts(s, vid)).get("deident", 0)
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "video.deident_skipped",
            DomainEvent.target_id == vid))).one()
    assert sent == 2
    assert ev.payload["attempts"] == sent
    assert ev.payload["counted_attempts"] == 0, "일시 오류는 상한에 세지 않는다"
