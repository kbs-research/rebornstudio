"""비식별화 얼굴 좌표 JSON 배선 테스트.

확인하는 것은 세 가지다.

  1. 잡에 좌표 JSON용 presigned PUT이 늘 실린다. 형식은 `application/json`이다.
  2. 워커가 올렸다고 보고한 경우에만 키를 남긴다. 올리지 않은 파일의 키를 지어내지 않는다.
  3. 영구 실패하면 스테이징 mp4와 함께 좌표 JSON도 지운다.

실 워커 없이 돈다. 저장소 호출은 이 모듈의 대역으로 바꾼다.
"""
import uuid

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Project, Video
from api_gateway.services.media import deident as deident_service
from api_gateway.services.media import stage_runs
from api_gateway.services.media.pipeline import open_next
from httpx import AsyncClient
from tests import _storage
from tests._helpers import new_project, upload_video

_DESCRIPTOR = {"uploaded": True, "schema_version": 1,
               "content_type": "application/json", "coordinate_space": "output_pixels"}


@pytest.fixture(autouse=True)
async def _isolate():
    """앞 테스트가 남긴 변환·publish·비식별화 활성 행을 비운다."""
    from tests._helpers import reset_transcode_active
    await reset_transcode_active()
    yield


@pytest.fixture
def puts(monkeypatch) -> list[dict]:
    """`build_jobs`가 요청한 presigned PUT의 인자를 모은다."""
    calls: list[dict] = []

    def _fake(**kw):
        """인자를 기록하고 키가 드러나는 가짜 URL을 돌려준다."""
        calls.append(kw)
        return f"https://store/{kw['key']}?sig=x"

    monkeypatch.setattr(
        "api_gateway.services.media.deident.storage.presign_put_for", _fake)
    return calls


@pytest.fixture
def store_ops(monkeypatch) -> dict[str, list]:
    """승격과 정리의 저장소 호출을 기록만 하는 대역으로 바꾼다."""
    ops: dict[str, list] = {"copy": [], "delete": []}
    monkeypatch.setattr("api_gateway.services.media.deident.storage.copy_object",
                        lambda **kw: ops["copy"].append(kw))
    monkeypatch.setattr("api_gateway.services.media.deident.storage.delete_object",
                        lambda **kw: ops["delete"].append(kw.get("key", "")))
    return ops


async def _running_job(client: AsyncClient, token: str, name: str) -> tuple[uuid.UUID, dict]:
    """비식별화를 켠 프로젝트에 영상을 올리고 잡을 하나 받아 돌려준다."""
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
        jobs = await deident_service.build_jobs(s)
    return vid, next(j for j in jobs if j["video_id"] == str(vid))


async def test_job_carries_a_json_put_for_the_coordinates(
    client: AsyncClient, admin_token: str, puts: list[dict],
):
    """잡에 좌표 JSON용 presigned PUT이 실린다. 키는 그 시도의 스테이징이고 형식은 JSON이다.

    Content-Type이 서명에 들어가므로 deid가 올리는 `application/json`과 다르면 PUT이 거절된다.
    최종 키(분석본 옆)에는 성공 판정이 옮긴다.
    """
    vid, job = await _running_job(client, admin_token, "deid-coords-job")
    assert deident_service.coordinates_key(vid) == f"transcoded/720p/{vid}.faces.json"
    key = deident_service.coordinates_staging_key(vid, job["token"])
    assert key.startswith(f"work/{vid}/{job['token']}/")
    assert job["coordinates_put_url"] == f"https://store/{key}?sig=x"
    call = next(c for c in puts if c["key"] == key)
    assert call["content_type"] == "application/json"
    # 영상 산출의 PUT은 그대로 mp4다.
    mp4 = next(c for c in puts if c["key"] == deident_service.deident_key(vid, job["token"]))
    assert mp4.get("content_type", "video/mp4") == "video/mp4"
    assert job["put_url"] != job["coordinates_put_url"], "두 산출이 같은 객체를 가리킨다"


async def test_completion_records_the_key_when_uploaded(
    client: AsyncClient, admin_token: str, puts: list[dict], store_ops: dict,
):
    """deid가 좌표를 올렸다고 보고하면 좌표를 최종 키로 옮기고 영상 이력과 단계 이력에 키를 남긴다."""
    vid, job = await _running_job(client, admin_token, "deid-coords-ok")
    async with SessionLocal() as s:
        ok = await deident_service.complete(
            s, vid, job["token"], ok=True, elapsed_s=12.0,
            report={"model": "face-anonymizer", "masked": True,
                    "detection_rate": 0.5, "coordinates": _DESCRIPTOR})
    assert ok is True

    expected = {
        "bucket": _storage.BUCKET,
        "key": deident_service.coordinates_key(vid),
        "schema_version": 1,
        "coordinate_space": "output_pixels",
    }
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "done"
        assert v.extra["deident"]["coordinates"] == expected
        # 다른 결과 필드는 그대로 남는다.
        assert v.extra["deident"]["detection_rate"] == 0.5
        run = (await stage_runs.latest_runs(s, vid))["deident"]
        assert run.detail["coordinates"] == expected
    moved = {(c["src_key"], c["dst_key"]) for c in store_ops["copy"]}
    assert (deident_service.coordinates_staging_key(vid, job["token"]),
            deident_service.coordinates_key(vid)) in moved


async def test_completion_keeps_the_device_and_notices(
    client: AsyncClient, admin_token: str, puts: list[dict], store_ops: dict,
):
    """보고의 실행 장치와 CPU 대체 표시를 영상 이력과 단계 이력에 그대로 남긴다.

    GPU 서버에서 CPU로 돈 처리는 결과가 정상이라 처리 시간 말고는 드러나지 않는다.
    """
    vid, job = await _running_job(client, admin_token, "deid-device")
    async with SessionLocal() as s:
        assert await deident_service.complete(
            s, vid, job["token"], ok=True, elapsed_s=30.0,
            report={"model": "essential-yunet", "masked": True, "device": "cpu",
                    "notices": ["cpu-fallback"]})
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.extra["deident"]["device"] == "cpu"
        run = (await stage_runs.latest_runs(s, vid))["deident"]
        assert run.detail["device"] == "cpu" and run.detail["notices"] == ["cpu-fallback"]


@pytest.mark.parametrize("report", [
    {"model": "face-anonymizer", "masked": True},
    {"model": "face-anonymizer", "masked": True, "coordinates": {"uploaded": False}},
    {"model": "face-anonymizer", "masked": True, "coordinates": "yes"},
    None,
])
async def test_completion_does_not_invent_a_key(
    client: AsyncClient, admin_token: str, puts: list[dict], store_ops: dict,
    report,
):
    """좌표를 올렸다는 보고가 없으면 키를 남기지 않는다.

    검증용 어댑터(`passthrough`)는 좌표를 만들지 않는다. 키를 남기면 기록이 없는 파일을 가리킨다.
    """
    vid, job = await _running_job(client, admin_token, "deid-coords-none")
    async with SessionLocal() as s:
        ok = await deident_service.complete(
            s, vid, job["token"], ok=True, elapsed_s=3.0, report=report)
    assert ok is True
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert "coordinates" not in v.extra["deident"]
        run = (await stage_runs.latest_runs(s, vid))["deident"]
        assert "coordinates" not in (run.detail or {})


async def test_permanent_failure_removes_the_coordinates_too(
    client: AsyncClient, admin_token: str, puts: list[dict], store_ops: dict,
):
    """영구 실패하면 좌표 JSON도 지운다. 좌표 JSON은 mp4보다 먼저 올라가므로 실패한 시도가 남겼을 수 있다."""
    vid, job = await _running_job(client, admin_token, "deid-coords-fail")
    for _ in range(3):
        async with SessionLocal() as s:
            await deident_service.complete(s, vid, job["token"], ok=False, error="모델 미구성")
            v = await s.get(Video, vid)
            if v.deident_status == "pending":
                jobs = await deident_service.build_jobs(s)
                job = next(j for j in jobs if j["video_id"] == str(vid))
    async with SessionLocal() as s:
        assert (await s.get(Video, vid)).deident_status == "failed"
    assert deident_service.coordinates_staging_key(vid, job["token"]) in store_ops["delete"]
    assert deident_service.deident_key(vid, job["token"]) in store_ops["delete"]
