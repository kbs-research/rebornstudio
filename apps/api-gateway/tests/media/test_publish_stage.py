"""publish 단계의 재생본 생성·분석본 확정·스테이징 정리 테스트.

publish는 변환과 따로 둔다. 두 단계 사이에 비식별화가 들어간다.
세 가지를 확인한다.

  1. transcode는 마스터만 만들어 스테이징에 둔다. 최종 분석본 위치는 건드리지 않는다.
     그 위치의 파일을 MI가 읽으므로 비식별화 전 파일을 그 위치에 두지 않는다.
  2. publish가 마스터를 최종 위치로 옮긴다(서버 쪽 복사). 워커가 데이터를 직접 옮기지 않는다.
  3. `h264 · 720p · mp4` 원본은 transcode를 건너뛰고 publish만 돈다.
"""
import uuid

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.media import publish as publish_service
from api_gateway.services.media import storage
from api_gateway.services.media import transcode as transcode_service
from api_gateway.services.media.transcode import analysis_key, master_key
from httpx import AsyncClient
from tests import _storage
from tests._helpers import new_project, upload_video


@pytest.fixture(autouse=True)
async def _isolate_active_rows():
    """테스트마다 활성 변환 행을 정리한다.

    앞 테스트가 남긴 pending/running이 in-flight 상한을 점유하면 이 파일의 잡이 나가지 않는다.
    이 경우 파일 단독 실행은 통과하고 전체 실행만 실패한다(`_helpers.reset_transcode_active`).
    """
    from tests._helpers import reset_transcode_active
    await reset_transcode_active()
    yield


async def _seed(client: AsyncClient, token: str, *, probe: dict, name: str) -> uuid.UUID:
    """프로젝트와 영상을 만들고 probe 메타를 넣는다."""
    pid = await new_project(client, token, name)
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": probe}
        await s.commit()
    return vid


async def test_transcode_makes_master_only_and_opens_publish(
    client: AsyncClient, admin_token: str, fake_ffmpeg
):
    """변환은 마스터 하나만 만들고 최종 분석본 위치는 건드리지 않는다."""
    from reborn_transcode import run_job

    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 1080},
                      name="pub-master")
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.transcode_status = "pending"
        await s.commit()
        jobs = await transcode_service.build_jobs(s)
    job = next(j for j in jobs if j["video_id"] == str(vid))

    assert [t["label"] for t in job["targets"]] == ["analysis-720p"], "재생본은 publish 몫"
    staged = transcode_service.staging_key(vid, job["token"], "analysis-720p.mp4")
    assert staged in job["targets"][0]["put_url"], "그 시도의 스테이징 키로 간다"

    r = run_job(job)
    async with SessionLocal() as s:
        assert await transcode_service.complete_transcode(
            s, vid, job["token"], ok=True, elapsed_s=r["elapsed_s"]) is True
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "done"
        assert v.publish_status == "pending", "다음 단계가 열려야 한다"
        assert v.analysis_uri is None, "확정은 publish가 한다 — 비식별화 전이라 아직 아니다"
    assert storage.object_exists(bucket=_storage.BUCKET, key=master_key(vid)) is True
    assert storage.object_exists(bucket=_storage.BUCKET, key=analysis_key(vid)) is False


async def test_publish_finalizes_master_and_cleans_staging(
    client: AsyncClient, admin_token: str, fake_ffmpeg
):
    """publish가 재생본을 만들고 마스터를 최종 위치로 옮긴 뒤 스테이징을 지운다."""
    from reborn_transcode import run_job

    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 1080},
                      name="pub-final")
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.transcode_status = "pending"
        await s.commit()
        tjobs = await transcode_service.build_jobs(s)
    tjob = next(j for j in tjobs if j["video_id"] == str(vid))
    run_job(tjob)
    async with SessionLocal() as s:
        await transcode_service.complete_transcode(s, vid, tjob["token"], ok=True)

    async with SessionLocal() as s:
        pjobs = await publish_service.build_jobs(s)
    pjob = next(j for j in pjobs if j["video_id"] == str(vid))
    assert [t["label"] for t in pjob["targets"]] == ["play-480p"]
    assert master_key(vid) in pjob["input_url"], "재생본은 **마스터에서** 뽑는다"

    r = run_job(pjob)
    async with SessionLocal() as s:
        assert await publish_service.complete(
            s, vid, pjob["token"], ok=True, elapsed_s=r["elapsed_s"]) is True

    b = _storage.BUCKET
    assert storage.object_exists(bucket=b, key=analysis_key(vid)) is True, "최종 자리로 확정"
    assert storage.object_exists(bucket=b, key=master_key(vid)) is False, "스테이징 정리"
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.publish_status == "done"
        assert v.analysis_uri == f"s3://{b}/{analysis_key(vid)}"


async def test_720p_source_skips_transcode_entirely(client: AsyncClient, admin_token: str):
    """`h264 · 720p · mp4`는 마스터가 필요 없어서 변환 잡을 만들지 않는다.

    재생본은 publish가 원본에서 바로 만든다.
    """
    vid = await _seed(client, admin_token, name="pub-skip", probe={
        "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"})
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.transcode_status = "pending"
        await s.commit()
        jobs = await transcode_service.build_jobs(s)

    assert all(j["video_id"] != str(vid) for j in jobs), "변환 잡이 안 나가야 한다"
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.transcode_status == "skipped"
        assert v.publish_status == "pending", "publish는 원본에서 바로 재생본을 뽑는다"


async def test_publish_waits_for_transcode(client: AsyncClient, admin_token: str):
    """마스터가 아직 없으면 publish 대상으로 고르지 않는다. 입력 없는 잡을 만들지 않는다."""
    vid = await _seed(client, admin_token, probe={"video_codec": "av1", "height": 1080},
                      name="pub-wait")
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.transcode_status = "running"   # 아직 도는 중
        v.publish_status = "pending"
        await s.commit()
        jobs = await publish_service.build_jobs(s)
    assert all(j["video_id"] != str(vid) for j in jobs)


async def test_stale_token_completion_rejected(client: AsyncClient, admin_token: str):
    """이전 시도의 완료 보고를 펜싱 토큰 검사로 버린다. transcode와 같은 안전장치다."""
    vid = await _seed(client, admin_token, name="pub-fence", probe={
        "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"})
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.publish_status = "pending"
        await s.commit()
        jobs = await publish_service.build_jobs(s)
    job = next(j for j in jobs if j["video_id"] == str(vid))

    async with SessionLocal() as s:
        assert await publish_service.complete(
            s, vid, "낡은-토큰", ok=True) is False
        v = await s.get(Video, vid)
        assert v.publish_status == "running", "엉뚱한 토큰이 상태를 바꾸면 안 된다"
    # 워커가 그 시도의 스테이징 키에 재생본을 올린 상태를 만든다.
    _storage.put_bytes(transcode_service.staging_key(vid, job["token"], "play-480p.mp4"),
                       b"play", content_type="video/mp4")
    async with SessionLocal() as s:
        assert await publish_service.complete(s, vid, job["token"], ok=True) is True


async def _intake_video(client: AsyncClient, admin_token: str, name: str) -> tuple[str, uuid.UUID]:
    """반입 스테이징을 실제로 거친 영상을 만든다. 원본이 `intake/` 키에 올라간 상태다."""
    from tests._helpers import new_project, upload_video
    pid = await new_project(client, admin_token, name)
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.source_uri.endswith(f"intake/{vid}/source"), "반입은 스테이징 프리픽스로"
        v.extra = {**(v.extra or {}), "probe": {
            "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"}}
        v.publish_status = "pending"
        await s.commit()
    return pid, vid


async def _run_publish(vid: uuid.UUID) -> None:
    """publish 잡을 만들어 실행하고 완료를 보고한다."""
    from reborn_transcode import run_job
    async with SessionLocal() as s:
        jobs = await publish_service.build_jobs(s)
    job = next(j for j in jobs if j["video_id"] == str(vid))
    r = run_job(job)
    async with SessionLocal() as s:
        assert await publish_service.complete(
            s, vid, job["token"], ok=True, elapsed_s=r["elapsed_s"]) is True


async def _finish_and_dispose(vid) -> None:
    """분석 완료와 체크섬 기록을 표시하고 원본 정리를 실행한다.

    원본 정리는 publish 다음에 따로 한다. publish 뒤에 MI 분석이 남아 있고 규격 내 원본은
    그 분석의 입력이다. 정리 조건은 `services/media/retention`에 있다.
    지우는 원본은 체크섬이 끝나야 정리하므로 체크섬도 기록해 둔다.
    """
    from api_gateway.models import AnalysisJob
    from api_gateway.services.media.retention import dispose_finished_sources

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        s.add(AnalysisJob(adapter_key="maiu", video_id=vid, project_id=v.project_id, status="done"))
        v.source_sha256 = v.source_sha256 or "sha256:test"
        await s.commit()
    async with SessionLocal() as s:
        await dispose_finished_sources(s)


async def test_source_deleted_when_project_does_not_retain(
    client: AsyncClient, admin_token: str, fake_ffmpeg
):
    """기본값(`retain_original=false`)이면 준비가 끝난 뒤 원본 객체를 지운다.

    되돌릴 수 없는 삭제라서 `source_uri`는 지우지 않는다.
    `source_uri`는 출처 기록이고 감사의 근거다.
    원본 객체가 남아 있는지는 `source_retained`로 판단한다.
    """
    from api_gateway.services.media import resolver

    _, vid = await _intake_video(client, admin_token, "keep-no")
    await _run_publish(vid)
    await _finish_and_dispose(vid)

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.source_retained is False
        assert v.source_uri is not None, "출처 기록은 남는다 — 있던 곳과 있는 곳은 다르다"
        bucket, key = v.source_uri.split("/")[2], "/".join(v.source_uri.split("/")[3:])
        assert storage.object_exists(bucket=bucket, key=key) is False
        # 없는 원본을 가리키는 재생 링크를 만들지 않는다. 재생본이 있으므로 재생본을 돌려준다.
        assert resolver.playback_source(v).variant == "playback_480p"
        assert resolver.media_uri(v, "transcode_input") is None


async def test_source_moved_to_archive_when_project_retains(
    client: AsyncClient, admin_token: str, fake_ffmpeg
):
    """`retain_original=true`면 원본을 보관 프리픽스로 옮긴다.

    복사한 뒤 삭제하므로 사본이 늘지 않는다.
    """
    from api_gateway.models import Project
    from api_gateway.services.media.video import archive_key

    pid, vid = await _intake_video(client, admin_token, "keep-yes")
    async with SessionLocal() as s:
        p = await s.get(Project, uuid.UUID(pid))
        p.retain_original = True
        await s.commit()

    await _run_publish(vid)
    await _finish_and_dispose(vid)

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.source_retained is True
        assert v.source_uri.endswith(archive_key(vid)), "URI가 보관 위치를 가리켜야 한다"
        bucket = v.source_uri.split("/")[2]
        assert storage.object_exists(bucket=bucket, key=archive_key(vid)) is True
        assert storage.object_exists(bucket=bucket, key=f"intake/{vid}/source") is False


async def test_bucket_registered_source_is_never_touched(
    client: AsyncClient, admin_token: str, fake_ffmpeg
):
    """버킷 등록 영상의 원본은 지우거나 옮기지 않는다.

    버킷 등록 원본은 원래 그 위치에 있던 외부 파일이라 이 시스템에 지울 권한이 없다.
    `intake/` 키에 있는 원본만 정리한다.
    """
    vid = await _seed(client, admin_token, name="keep-foreign", probe={
        "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"})
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.source_uri = "s3://someone-elses/news/original.mp4"
        v.publish_status = "pending"
        await s.commit()
    # 프로젝트 버킷이 아닌 원본은 그 영상에 고정 기록한 연결로 읽는다. 그래서 바꾼 원본 위치를 메모리에 다시 적재한다.
    await _storage.reset_memory()

    await _run_publish(vid)
    await _finish_and_dispose(vid)

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.source_retained is True
        assert v.source_uri == "s3://someone-elses/news/original.mp4"

    # 원본 상태는 영상 행의 `source_retained`가 기록한다.
    # 원본 정리는 publish 뒤에 따로 하므로 publish 시점에는 원본 유지 여부가 정해지지 않았다.
    # 그래서 `publish` 이력에는 원본 상태를 적지 않는다.
    from api_gateway.models import MediaStageRun
    from sqlalchemy import select as _select
    async with SessionLocal() as s:
        run = (await s.scalars(
            _select(MediaStageRun).where(
                MediaStageRun.video_id == vid, MediaStageRun.stage == "publish",
                MediaStageRun.status == "ok")
        )).first()
        assert "source" not in (run.detail or {}), \
            "publish가 원본에 대해 아무 말도 하면 안 된다 — 치우는 쪽이 아니다"
