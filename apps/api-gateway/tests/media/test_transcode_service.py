"""DB 없는 워커의 잡 프로토콜 테스트.

build_jobs(생산), run_job(소비), complete(판정)로 이어지는 흐름이다.
세 가지를 확인한다.
  1. build_jobs가 만든 페이로드만으로(자격 증명·DB 없이) run_job이 인코딩과 업로드를
     끝낸다. 실제 httpx PUT(moto presigned URL)까지 거친다.
  2. 완료 보고의 판정(펜싱, 성공 기록, 일시 오류의 시도 횟수 되돌리기, 영구 오류 상한)은 DB 쪽 한 곳에서 한다.
  3. 업스케일 금지와 사양(crf) 계산은 생산자(build)가 맡는다.
"""
import uuid

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.media import storage
from api_gateway.services.media import transcode as transcode_service
from httpx import AsyncClient
from tests import _storage
from tests._helpers import new_project, reset_transcode_active, upload_video


@pytest.fixture(autouse=True)
async def _isolate_transcode_rows():
    """변환 활성 행을 격리한다. 근거는 `_helpers.reset_transcode_active`의 docstring에 있다."""
    await reset_transcode_active()
    yield


async def _seed(
    client: AsyncClient, admin_token: str, *, probe: dict, attempts: int = 0
) -> uuid.UUID:
    """probe 결과를 넣고 변환이 pending인 영상을 만든다."""
    pid = await new_project(client, admin_token, "m5")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": probe}
        v.transcode_status = "pending"
        v.transcode_attempts = attempts
        await s.commit()
    return vid


async def _build_one(vid: uuid.UUID) -> dict:
    """build_jobs를 한 번 돌리고 이 영상의 잡을 돌려준다."""
    async with SessionLocal() as s:
        jobs = await transcode_service.build_jobs(s)
    job = next(j for j in jobs if j["video_id"] == str(vid))
    return job


async def test_full_loop_job_payload_only(client: AsyncClient, admin_token: str, fake_ffmpeg):
    """페이로드만으로 run_job이 끝까지 돈다(실제 PUT).

    complete 후 상태는 done이고 산출물은 스테이징에 있다.
    analysis_uri는 아직 비어 있다. 확정은 publish가 한다.
    """
    from reborn_transcode import run_job

    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 1080})
    job = await _build_one(vid)

    # 생산자 계약: 사양은 build가 계산한다(업스케일 금지, crf 값).
    # 변환은 마스터 하나만 만들고 재생본은 publish가 만든다.
    assert [(t["height"], t["crf"]) for t in job["targets"]] == [(720, 24)]
    assert job["input_url"].startswith("http")
    assert all(t["put_url"].startswith("http") for t in job["targets"])
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "running", "build가 리스·토큰까지 건다"
        assert v.transcode_token == job["token"]

    beats: list[float] = []
    result = run_job(job, on_heartbeat=beats.append)   # DB 없이 페이로드만 입력으로 쓴다
    assert result["elapsed_s"] >= 0

    async with SessionLocal() as s:
        ok = await transcode_service.complete_transcode(
            s, vid, job["token"], ok=True, elapsed_s=result["elapsed_s"])
    assert ok is True
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        bucket = v.source_uri.split("/")[2]
        assert v.transcode_status == "done"
        assert v.publish_status == "pending", "다음 단계(publish)가 열린다"
        # 확정은 publish가 한다. 그 사이에 비식별화가 들어간다.
        assert v.analysis_uri is None
        assert v.extra["transcode"]["reason"] == "codec:av1,height:1080"
    # 마스터가 스테이징에 있으면 presigned PUT이 실제로 업로드를 마쳤다.
    assert storage.object_exists(bucket=bucket, key=f"work/{vid}/analysis-720p.mp4") is True
    assert storage.object_exists(bucket=bucket, key=f"transcoded/720p/{vid}.mp4") is False


async def test_build_no_upscale(client: AsyncClient, admin_token: str):
    """720p av1 원본의 마스터는 scale 없이 코덱만 바꾼다(업스케일 금지). 판정은 생산자가 한다."""
    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 720})
    job = await _build_one(vid)
    assert [t["height"] for t in job["targets"]] == [None]


async def test_stale_token_completion_rejected(client: AsyncClient, admin_token: str):
    """리스를 잃은 이전 시도의 완료 보고를 펜싱으로 버린다."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 1080})
    job_old = await _build_one(vid)
    # 리스를 만료시키고 회수한 뒤 새 토큰으로 잡을 다시 만든다.
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == vid).values(
            transcode_lease_until=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
        assert await transcode_service.reclaim_expired(s) >= 1
    job_new = await _build_one(vid)
    assert job_new["token"] != job_old["token"]

    async with SessionLocal() as s:
        assert await transcode_service.complete_transcode(
            s, vid, job_old["token"], ok=True) is False, "낡은 보고가 통과했다"
        v = await s.get(Video, vid)
        assert v.transcode_status == "running" and v.analysis_uri is None


async def test_transient_failure_refunds_attempt(client: AsyncClient, admin_token: str):
    """일시 실패를 보고하면 pending으로 돌리고 시도 횟수를 되돌리고 `[일시]`로 기록한다."""
    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 1080})
    job = await _build_one(vid)
    async with SessionLocal() as s:
        await transcode_service.complete_transcode(
            s, vid, job["token"], ok=False, error="업로드 실패 HTTP 503", transient=True)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "pending"
        assert v.transcode_attempts == 0, "일시는 환불(build의 +1 상쇄)"
        assert v.extra["transcode_error"].startswith("[일시]")


async def test_permanent_failure_caps(client: AsyncClient, admin_token: str):
    """영구 실패는 상한에 도달하면 failed로 확정하고, 미만이면 pending으로 재시도한다."""
    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 1080})
    job = await _build_one(vid)
    async with SessionLocal() as s:
        await transcode_service.complete_transcode(
            s, vid, job["token"], ok=False, error="ffmpeg failed rc=1", transient=False)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "pending" and v.transcode_attempts == 1

    vid2 = await _seed(client, admin_token,
                       probe={"video_codec": "av1", "height": 1080},
                       attempts=transcode_service._MAX_RETRIES - 1)
    job2 = await _build_one(vid2)
    async with SessionLocal() as s:
        await transcode_service.complete_transcode(
            s, vid2, job2["token"], ok=False, error="bad file", transient=False)
    async with SessionLocal() as s:
        v = await s.get(Video, vid2)
        assert v.transcode_status == "failed"
        assert "bad file" in v.extra["transcode_error"]


async def test_heartbeat_extends_lease_with_fencing(client: AsyncClient, admin_token: str):
    """하트비트(큐 메시지의 DB 대리)는 현재 토큰일 때만 리스를 연장한다."""
    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 1080})
    job = await _build_one(vid)
    async with SessionLocal() as s:
        assert await transcode_service.extend_lease(s, vid, job["token"]) is True
        assert await transcode_service.extend_lease(s, vid, "stale-token") is False


def _run_job_with(monkeypatch, *, put_status: int = 200, ffmpeg_error: str | None = None):
    """러너를 가짜 ffmpeg와 가짜 PUT으로 돌리고 난 `JobError`를 돌려준다."""
    import httpx as hx
    import reborn_transcode.job_runner as jr
    from reborn_transcode import JobError, run_job
    from reborn_transcode.transcode import TranscodeError

    def _fake_ffmpeg(i, o, s, *, on_progress=None, cancel=None, ffmpeg="ffmpeg"):
        """출력 파일을 쓰거나 지정한 ffmpeg 오류를 낸다."""
        if ffmpeg_error is not None:
            raise TranscodeError(ffmpeg_error)
        o.write_bytes(b"\x00")

    def _put(*a, **kw):
        """지정한 상태 코드로 응답한다."""
        return type("R", (), {"status_code": put_status})()

    monkeypatch.setattr(jr, "transcode_streaming", _fake_ffmpeg)
    monkeypatch.setattr(hx, "put", _put)
    monkeypatch.setattr(jr.httpx, "put", _put)
    job = {"video_id": "v", "token": "t", "input_url": "http://x",
           "targets": [{"label": "a", "height": None, "crf": 24,
                        "put_url": "http://put", "content_type": "video/mp4"}],
           "heartbeat_every_s": 60}
    with pytest.raises(JobError) as e:
        run_job(job)
    return e.value


@pytest.mark.parametrize(("status", "transient"), [(503, True), (500, True), (403, True),
                                                   (404, False), (400, False)])
def test_job_runner_classifies_put_failure(monkeypatch, status: int, transient: bool):
    """러너가 업로드 PUT 실패를 상태 코드로 나눠 5xx·403은 일시 오류로, 나머지 4xx는 영구 오류로 분류하는지 확인한다.

    403은 presign 만료일 수 있어 다시 서명하면 낫는다. 판정 근거는 워커가 만든다.
    """
    err = _run_job_with(monkeypatch, put_status=status)
    assert f"HTTP {status}" in str(err)
    assert err.transient is transient


def test_job_runner_classifies_ffmpeg_failure_as_permanent(monkeypatch):
    """ffmpeg 오류는 같은 입력으로 다시 돌려도 결과가 같으므로 영구 오류로 분류하는지 확인한다."""
    err = _run_job_with(monkeypatch, ffmpeg_error="ffmpeg failed rc=1")
    assert "ffmpeg failed rc=1" in str(err)
    assert err.transient is False


async def test_playback_only_source_never_reaches_transcode(
    client: AsyncClient, admin_token: str
):
    """`h264 · 720p · mp4` 원본은 변환 잡을 만들지 않는다.

    이런 원본은 분석본이 필요 없고, 재생본은 publish가 원본에서 바로 만든다.
    분석본이 없으면 MI는 원본을 그대로 입력으로 쓴다(`resolver.media_uri`의 `analysis_input` 폴백).
    """
    from api_gateway.services.media import resolver

    vid = await _seed(client, admin_token, probe={
        "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"})
    async with SessionLocal() as s:
        jobs = await transcode_service.build_jobs(s)
    assert all(j["video_id"] != str(vid) for j in jobs)

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "skipped"
        assert v.publish_status == "pending"
        assert v.analysis_uri is None
        assert resolver.media_uri(v, "analysis_input") == v.source_uri


async def test_replanned_to_nothing_ends_without_worker(client: AsyncClient, admin_token: str):
    """큐에 들어온 뒤 재검사로 변환이 필요 없어지면 워커를 호출하지 않고 `skipped`로 끝낸다.

    끝내지 않으면 pending이 계속 남아 beat마다 산출이 0개인 잡을 만든다.
    """
    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 1080})
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": {
            "video_codec": "h264", "height": 480, "format_name": "mov,mp4,m4a"}}
        await s.commit()
        jobs = await transcode_service.build_jobs(s)
    assert all(j["video_id"] != str(vid) for j in jobs)
    async with SessionLocal() as s:
        assert (await s.get(Video, vid)).transcode_status == "skipped"


async def test_targets_carry_per_output_preset(client: AsyncClient, admin_token: str):
    """인코딩 프리셋을 산출마다 다르게 싣는다.

    두 산출은 쓰는 곳이 다르다. 재생본은 사람이 보는 프록시다.
    분석본은 MI와 비식별화의 입력이다.
    프리셋을 하나로 통일하면 재생본 인코딩이 느려지거나 분석본 화질이 떨어진다.
    """
    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 1080})
    job = await _build_one(vid)
    by = {t["label"]: t for t in job["targets"]}
    assert by["analysis-720p"]["preset"] == "fast"   # MI·비식별화 입력이라 바꾸지 않는다
    assert all("threads" in t for t in job["targets"])

    # 재생본은 publish가 만든다. 프록시라서 veryfast(1.7배)를 쓴다.
    from api_gateway.services.media import publish as publish_service
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.transcode_status = "skipped"
        v.publish_status = "pending"
        await s.commit()
        pjobs = await publish_service.build_jobs(s)
    pjob = next(j for j in pjobs if j["video_id"] == str(vid))
    assert pjob["targets"][0]["preset"] == "veryfast"


async def test_outputs_go_to_configured_bucket_not_source_bucket(
    client: AsyncClient, admin_token: str
):
    """산출은 프로젝트에 설정한 버킷에 올린다. 원본 버킷과 무관하다.

    `source_uri.split("/")[2]`처럼 원본 버킷을 따르면 산출이 영상의 등록 경로에 따라 흩어진다.
    원본이 클라우드에 없는 로컬 반입에는 쓸 수도 없다.
    """

    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 1080})
    # 원본을 다른 버킷에 둔 것처럼 만든다. 버킷 등록 경로가 이런 상태를 만든다.
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.source_uri = "s3://some-other-bucket/incoming/x.mp4"
        await s.commit()
    # 프로젝트 버킷이 아닌 원본은 그 영상에 고정 기록한 연결로 읽는다. 그래서 바꾼 원본 위치를 메모리에 다시 적재한다.
    await _storage.reset_memory()

    job = await _build_one(vid)
    assert "some-other-bucket" in job["input_url"], "입력은 원본이 있는 곳에서 읽는다"
    for t in job["targets"]:
        assert _storage.BUCKET in t["put_url"], "산출은 설정된 버킷으로만 간다"
        assert "some-other-bucket" not in t["put_url"]
