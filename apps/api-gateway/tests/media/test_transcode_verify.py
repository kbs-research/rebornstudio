"""변환 산출 검사 테스트. 판정은 회신을 받는 서버 한 곳에서 한다.

워커는 산출 측정값(`outputs`)을 싣기만 한다.
`complete_transcode`와 `publish.complete`가 그 값을 원본 probe와 비교한다
(`transcode.judge_outputs` → `reborn_transcode.output_check`).
네 가지를 확인한다.

  1. 길이 차가 기준(0.5초)을 넘으면 영구 실패다. 시도 횟수를 되돌리지 않는다.
     사유는 운영자용 문구로 준비 타임라인에 싣고 불합격한 산출은 지운다.
  2. 기준 안이면 통과다. 측정값은 `extra`에 병합 저장하고(다른 키를 지우지 않는다)
     단계 이력에도 남긴다. 재생본 확정이 `extra.transcode`를 다시 써도 분석본 측정값은 남는다.
  3. 상한에 걸려 영구 실패한 변환은 `변환 재시도`로 다시 돌린다. 재생본도 같은 경로다.
  4. 이전 버전 워커(측정값 없음)의 보고는 그대로 받는다. 이전 토큰의 보고는 새 시도의 산출을
     지우지 못한다.

워커 쪽은 실제 코드가 돈다(`run_job`의 스레드와 presigned PUT까지).
가짜는 인코딩(`fake_ffmpeg`)과 산출을 읽는 ffprobe(`job_runner.probe`) 둘뿐이다.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import reborn_transcode.job_runner as jr
from api_gateway.database import SessionLocal
from api_gateway.models import MediaStageRun, Video
from api_gateway.services.media import publish as publish_service
from api_gateway.services.media import storage
from api_gateway.services.media import transcode as transcode_service
from api_gateway.services.media.transcode import master_key, playback_key, staging_key
from httpx import AsyncClient
from reborn_transcode import ProbeTimeoutError, run_job
from sqlalchemy import select, update
from tests import _storage
from tests._helpers import new_project, reset_transcode_active, upload_video

#: 원본 probe. 10분짜리 av1 1080p라서 분석본 변환 대상이다(코덱·해상도).
SRC = {"video_codec": "av1", "width": 1920, "height": 1080, "duration_seconds": 600.0,
       "start_time_seconds": 0.0, "format_name": "mov,mp4,m4a,3gp,3g2,mj2"}


def _probed(seconds: float, **over) -> dict:
    """워커가 산출에서 읽는 `probe()` 결과 모양을 만든다. 주로 길이만 바꿔 쓴다."""
    return {"duration_seconds": seconds, "size_bytes": 4096,
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2", "major_brand": "isom",
            "video_codec": "h264", "width": 1280, "height": 720, "fps": 29.97,
            "start_time_seconds": 0.0, **over}


@pytest.fixture(autouse=True)
async def _isolate():
    """변환 활성 행을 격리한다. 근거는 `_helpers.reset_transcode_active`의 docstring에 있다."""
    await reset_transcode_active()
    yield


def _auth(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _seed(client: AsyncClient, token: str, name: str, *,
                attempts: int = 0, extra: dict | None = None) -> tuple[uuid.UUID, str]:
    """변환 대기 영상 하나를 만든다. 원본 probe와, 주어지면 다른 쓰기가 남긴 키를 넣는다."""
    pid = await new_project(client, token, name)
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": SRC, **(extra or {})}
        v.transcode_status = "pending"
        v.transcode_attempts = attempts
        await s.commit()
    return vid, pid


async def _transcode_job(vid: uuid.UUID) -> dict:
    """변환 build_jobs를 돌리고 이 영상의 잡을 돌려준다."""
    async with SessionLocal() as s:
        jobs = await transcode_service.build_jobs(s)
    return next(j for j in jobs if j["video_id"] == str(vid))


async def _publish_job(vid: uuid.UUID) -> dict:
    """publish build_jobs를 돌리고 이 영상의 잡을 돌려준다."""
    async with SessionLocal() as s:
        jobs = await publish_service.build_jobs(s)
    return next(j for j in jobs if j["video_id"] == str(vid))


async def _last_run(vid: uuid.UUID, stage: str) -> MediaStageRun:
    """이 영상의 해당 단계 이력 중 마지막 행을 읽는다."""
    async with SessionLocal() as s:
        return (await s.scalars(
            select(MediaStageRun)
            .where(MediaStageRun.video_id == vid, MediaStageRun.stage == stage)
            .order_by(MediaStageRun.id.desc()).limit(1)
        )).one()


# ── 분석본 ──────────────────────────────────────────────────────────────────


async def test_a_short_master_is_a_permanent_failure(
    client: AsyncClient, admin_token: str, fake_ffmpeg, monkeypatch,
):
    """워커가 성공으로 보고해도 분석본이 5분 짧으면 서버가 실패로 판정한다."""
    monkeypatch.setattr(jr, "probe", lambda uri, **kw: _probed(300.0))
    vid, _ = await _seed(client, admin_token, "d05-short")
    job = await _transcode_job(vid)
    result = run_job(job)                         # DB 없는 워커가 측정값을 싣는다
    assert result["outputs"][0]["duration_seconds"] == 300.0
    staged = staging_key(vid, job["token"], "analysis-720p.mp4")
    assert storage.object_exists(bucket=_storage.BUCKET, key=staged) is True

    async with SessionLocal() as s:
        assert await transcode_service.complete_transcode(
            s, vid, job["token"], ok=True, elapsed_s=result["elapsed_s"],
            outputs=result["outputs"]) is False

    said = "분석본 길이가 원본보다 5분 짧습니다. 허용 오차는 0.5초입니다"
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "pending", "상한 전이라 자동으로 다시 돈다"
        assert v.transcode_attempts == 1, "영구 — 일시처럼 시도를 환불하지 않는다"
        assert v.extra["transcode_error"] == said, "[일시] 표식이 없다"
        assert "transcode_output" not in v.extra, "떨어진 산출의 실측은 산출 메타가 아니다"
    run = await _last_run(vid, "transcode")
    assert run.status == "failed" and run.error == said
    assert run.detail["cause"] == "output_duration_mismatch"
    assert run.detail["transient"] is False
    assert run.detail["measured"]["expected_seconds"] == 600.0
    assert run.detail["measured"]["delta_seconds"] == -300.0
    assert storage.object_exists(bucket=_storage.BUCKET, key=staged) is False, \
        "떨어진 사본은 치운다"
    assert storage.object_exists(bucket=_storage.BUCKET, key=master_key(vid)) is False


async def test_a_matching_master_passes_and_its_facts_are_merged(
    client: AsyncClient, admin_token: str, fake_ffmpeg, monkeypatch,
):
    """길이가 기준 안인 분석본은 통과하고 측정값을 extra에 병합 저장한다."""
    monkeypatch.setattr(jr, "probe", lambda uri, **kw: _probed(600.02))
    vid, _ = await _seed(client, admin_token, "d05-ok",
                         extra={"broadcast": {"channel": "샘플1"}})
    job = await _transcode_job(vid)
    result = run_job(job)
    async with SessionLocal() as s:
        assert await transcode_service.complete_transcode(
            s, vid, job["token"], ok=True, elapsed_s=result["elapsed_s"],
            outputs=result["outputs"]) is True

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "done"
        assert v.extra["transcode_output"] == {
            "label": "analysis-720p", "duration_seconds": 600.02, "fps": 29.97,
            "video_codec": "h264", "width": 1280, "height": 720, "size_bytes": 4096,
            "expected_seconds": 600.0, "delta_seconds": 0.02, "tolerance_seconds": 0.5,
            "cause": None,
        }
        # 병합 저장이다. 다른 쓰기(원본 probe, 방송 메타 `broadcast`)가 남긴 키를 지우지 않는다.
        assert v.extra["probe"] == SRC
        assert v.extra["broadcast"] == {"channel": "샘플1"}
        assert v.extra["transcode"]["master_key"] == master_key(vid)
    run = await _last_run(vid, "transcode")
    assert run.status == "ok"
    assert run.detail["measured"]["video_codec"] == "h264"
    assert run.detail["measured"]["delta_seconds"] == 0.02


async def test_an_unreadable_master_is_a_failure(client: AsyncClient, admin_token: str):
    """읽지 못한 산출도 실패로 판정한다.

    원문은 진단용으로 이력에만 남기고 화면에는 운영자용 문구만 표시한다.
    """
    vid, _ = await _seed(client, admin_token, "d05-unreadable")
    job = await _transcode_job(vid)
    async with SessionLocal() as s:
        await transcode_service.complete_transcode(
            s, vid, job["token"], ok=True,
            outputs=[{"label": "analysis-720p",
                      "error": "ffprobe failed (rc=1): Invalid data found"}])
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "pending"
        assert v.extra["transcode_error"] == "분석본 파일을 읽지 못했습니다"
    run = await _last_run(vid, "transcode")
    assert run.detail["cause"] == "output_unreadable"
    assert "Invalid data" in run.detail["measured"]["probe_error"]
    assert "ffprobe" not in (run.error or "")


async def test_a_capped_failure_shows_its_reason_and_comes_back_with_retry(
    client: AsyncClient, admin_token: str,
):
    """상한에 걸려 영구 실패하면 준비 타임라인이 사유를 싣고, `변환 재시도`로 다시 돌린다."""
    vid, pid = await _seed(client, admin_token, "d05-capped",
                           attempts=transcode_service._MAX_RETRIES - 1)
    job = await _transcode_job(vid)
    async with SessionLocal() as s:
        await transcode_service.complete_transcode(
            s, vid, job["token"], ok=True,
            outputs=[{"label": "analysis-720p", "duration_seconds": 598.2}])
    said = "분석본 길이가 원본보다 1.8초 짧습니다. 허용 오차는 0.5초입니다"
    async with SessionLocal() as s:
        assert (await s.get(Video, vid)).transcode_status == "failed"

    # 영상 상세 준비 타임라인의 `사유 보기`가 표시하는 값이다.
    r = await client.get(f"/videos/{vid}/prep-timeline", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    entry = next(e for e in r.json()["entries"] if e["stage"] == "transcode")
    assert entry["status"] == "failed" and entry["error"] == said
    assert (await _last_run(vid, "transcode")).detail["cause"] == "output_duration_mismatch"

    r = await client.post(f"/projects/{pid}/videos/{vid}/transcode/retry",
                          headers=_auth(admin_token))
    assert r.status_code == 204, r.text
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "pending" and v.transcode_attempts == 0
        assert "transcode_error" not in (v.extra or {})


async def test_an_old_worker_without_facts_is_taken_as_before(
    client: AsyncClient, admin_token: str,
):
    """이전 버전 워커(측정값 없음)의 보고는 실패로 세지 않고 산출 메타도 지어내지 않는다."""
    vid, _ = await _seed(client, admin_token, "d05-legacy")
    job = await _transcode_job(vid)
    _storage.put_bytes(staging_key(vid, job["token"], "analysis-720p.mp4"), b"m",
                       content_type="video/mp4")
    async with SessionLocal() as s:
        assert await transcode_service.complete_transcode(
            s, vid, job["token"], ok=True) is True
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "done"
        assert "transcode_output" not in v.extra
    assert "measured" not in (await _last_run(vid, "transcode")).detail


async def test_a_stale_report_cannot_discard_the_new_attempts_output(
    client: AsyncClient, admin_token: str, fake_ffmpeg, monkeypatch,
):
    """이전 토큰의 보고는 아무것도 쓰지 못한다. 새 시도가 올린 정상 마스터도 지우지 못한다."""
    monkeypatch.setattr(jr, "probe", lambda uri, **kw: _probed(600.0))
    vid, _ = await _seed(client, admin_token, "d05-stale")
    old = await _transcode_job(vid)
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == vid).values(
            transcode_lease_until=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
        assert await transcode_service.reclaim_expired(s) >= 1
    new = await _transcode_job(vid)
    run_job(new)                                  # 새 시도가 마스터를 올린다
    staged = staging_key(vid, new["token"], "analysis-720p.mp4")
    assert storage.object_exists(bucket=_storage.BUCKET, key=staged) is True

    async with SessionLocal() as s:
        assert await transcode_service.complete_transcode(
            s, vid, old["token"], ok=True,
            outputs=[{"label": "analysis-720p", "duration_seconds": 10.0}]) is False
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "running", "새 시도는 그대로 돈다"
        assert "transcode_error" not in (v.extra or {})
    assert storage.object_exists(bucket=_storage.BUCKET, key=staged) is True


# ── 재생본 ──────────────────────────────────────────────────────────────────


async def _to_publish(client: AsyncClient, token: str, name: str, monkeypatch, *,
                      publish_attempts: int = 0) -> tuple[uuid.UUID, str]:
    """분석본 변환을 통과시켜 재생본 변환 대기 상태로 만든다. 이때 분석본 측정값이 저장된다."""
    monkeypatch.setattr(jr, "probe", lambda uri, **kw: _probed(600.0))
    vid, pid = await _seed(client, token, name)
    job = await _transcode_job(vid)
    r = run_job(job)
    async with SessionLocal() as s:
        assert await transcode_service.complete_transcode(
            s, vid, job["token"], ok=True, outputs=r["outputs"]) is True
    if publish_attempts:
        async with SessionLocal() as s:
            await s.execute(update(Video).where(Video.video_id == vid)
                            .values(publish_attempts=publish_attempts))
            await s.commit()
    return vid, pid


async def test_a_short_playback_copy_is_not_finalized(
    client: AsyncClient, admin_token: str, fake_ffmpeg, monkeypatch,
):
    """재생본이 3분 짧으면 확정하지 않는다. 불합격한 재생본은 지우고 마스터는 남긴다."""
    vid, _ = await _to_publish(client, admin_token, "d05-pub-short", monkeypatch)
    pjob = await _publish_job(vid)
    monkeypatch.setattr(jr, "probe", lambda uri, **kw: _probed(420.0, width=854, height=480))
    r = run_job(pjob)
    staged = staging_key(vid, pjob["token"], "play-480p.mp4")
    assert storage.object_exists(bucket=_storage.BUCKET, key=staged) is True

    async with SessionLocal() as s:
        assert await publish_service.complete(
            s, vid, pjob["token"], ok=True, outputs=r["outputs"]) is False
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.publish_status == "pending" and v.publish_attempts == 1
        assert v.extra["publish_error"] == (
            "재생본 길이가 원본보다 3분 짧습니다. 허용 오차는 0.5초입니다")
        assert v.analysis_uri is None, "떨어진 확정은 분석본을 최종 자리로 옮기지 않는다"
        assert "playback_key" not in (v.extra.get("transcode") or {}), \
            "재생 링크가 깨진 사본을 가리키지 않는다"
    run = await _last_run(vid, "publish")
    assert run.detail["cause"] == "output_duration_mismatch"
    assert run.detail["measured"]["label"] == "play-480p"
    assert storage.object_exists(bucket=_storage.BUCKET, key=staged) is False
    assert storage.object_exists(bucket=_storage.BUCKET, key=playback_key(vid)) is False
    assert storage.object_exists(bucket=_storage.BUCKET, key=master_key(vid)) is True, \
        "다음 시도의 입력(마스터)은 남긴다"


async def test_a_matching_playback_copy_keeps_both_facts(
    client: AsyncClient, admin_token: str, fake_ffmpeg, monkeypatch,
):
    """길이가 기준 안인 재생본은 확정되고 분석본과 재생본의 측정값이 모두 남는다."""
    vid, _ = await _to_publish(client, admin_token, "d05-pub-ok", monkeypatch)
    pjob = await _publish_job(vid)
    monkeypatch.setattr(jr, "probe", lambda uri, **kw: _probed(599.9, width=854, height=480))
    r = run_job(pjob)
    async with SessionLocal() as s:
        assert await publish_service.complete(
            s, vid, pjob["token"], ok=True, outputs=r["outputs"]) is True
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.publish_status == "done"
        assert v.extra["publish_output"]["height"] == 480
        assert v.extra["publish_output"]["delta_seconds"] == -0.1
        # 재생본 확정은 `extra.transcode`를 통째로 다시 쓴다.
        # 분석본 측정값은 다른 키(`transcode_output`)에 있으므로 그대로 남는다.
        assert v.extra["transcode"]["playback_key"] == playback_key(vid)
        assert v.extra["transcode_output"]["label"] == "analysis-720p"
    run = await _last_run(vid, "publish")
    assert run.status == "ok" and run.detail["measured"]["label"] == "play-480p"


async def test_a_capped_playback_failure_comes_back_with_the_same_retry(
    client: AsyncClient, admin_token: str, fake_ffmpeg, monkeypatch,
):
    """재생본 변환이 영구 실패해도 같은 `변환 재시도`로 다시 돌린다.

    분석본 변환은 건드리지 않는다.
    """
    vid, pid = await _to_publish(client, admin_token, "d05-pub-capped", monkeypatch,
                                 publish_attempts=publish_service._MAX_RETRIES - 1)
    pjob = await _publish_job(vid)
    async with SessionLocal() as s:
        await publish_service.complete(
            s, vid, pjob["token"], ok=True,
            outputs=[{"label": "play-480p", "duration_seconds": 30.0}])
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.publish_status == "failed" and v.transcode_status == "done"

    r = await client.post(f"/projects/{pid}/videos/{vid}/transcode/retry",
                          headers=_auth(admin_token))
    assert r.status_code == 204, r.text
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.publish_status == "pending" and v.publish_attempts == 0
        assert v.transcode_status == "done", "분석본 변환은 건드리지 않는다"
        assert "publish_error" not in (v.extra or {})


def _timeout_probe(uri, **kw):
    """산출물 ffprobe가 제한 시간을 넘긴 경우를 흉내 낸다. 실제 probe가 내는 예외와 같다."""
    raise ProbeTimeoutError(f"ffprobe timeout after {kw.get('timeout')}s")


async def test_a_playback_check_timeout_is_transient_and_keeps_the_attempt(
    client: AsyncClient, admin_token: str, fake_ffmpeg, monkeypatch,
):
    """산출물 검사의 시간 초과는 일시 오류로 처리한다. 시도를 쓰지 않고 다시 돈다.

    부하가 높은 서버에서는 로컬 파일의 헤더를 읽는 ffprobe도 제한 시간을 넘긴다.
    검사 시간 초과를 "파일을 읽지 못했습니다"와 같은 영구 실패로 세면 긴 변환을 버리고 시도를 소모한다.
    그러면 상한에서 실패로 확정된다. 사유에는 실제 원인(검사 시간 초과)을 적는다.
    """
    vid, _ = await _to_publish(client, admin_token, "n01-pub-timeout", monkeypatch,
                               publish_attempts=publish_service._MAX_RETRIES - 1)
    pjob = await _publish_job(vid)
    monkeypatch.setattr(jr, "probe", _timeout_probe)
    r = run_job(pjob)
    async with SessionLocal() as s:
        assert await publish_service.complete(
            s, vid, pjob["token"], ok=True, outputs=r["outputs"]) is False
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.publish_status == "pending", "상한에 닿아도 일시 오류는 실패로 확정하지 않는다"
        assert v.publish_attempts == publish_service._MAX_RETRIES - 1, "시도를 환불한다"
        assert v.extra["publish_error"] == "[일시] 재생본 검사가 시간 안에 끝나지 않았습니다"
    run = await _last_run(vid, "publish")
    assert run.detail["transient"] is True
    assert run.detail["cause"] == "output_check_timeout"
    assert run.error == "재생본 검사가 시간 안에 끝나지 않았습니다"


async def test_a_master_check_timeout_is_transient(client: AsyncClient, admin_token: str):
    """분석본에도 같은 규칙을 적용한다. 판정은 `judge_outputs` 한 곳에서 하므로 두 단계의 판정이 같다."""
    vid, _ = await _seed(client, admin_token, "n01-master-timeout",
                         attempts=transcode_service._MAX_RETRIES - 1)
    job = await _transcode_job(vid)
    async with SessionLocal() as s:
        await transcode_service.complete_transcode(
            s, vid, job["token"], ok=True,
            outputs=[{"label": "analysis-720p", "error": "ffprobe timeout after 60s",
                      "timed_out": True}])
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "pending"
        assert v.transcode_attempts == transcode_service._MAX_RETRIES - 1
    run = await _last_run(vid, "transcode")
    assert run.detail["transient"] is True and run.detail["cause"] == "output_check_timeout"
