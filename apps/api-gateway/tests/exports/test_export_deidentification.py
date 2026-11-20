"""납품 메타의 비식별화 정보 테스트. 처리 여부·방식·재확인 표시와 리포트 집계를 확인한다.

가린 영역이 없는 영상은 처리했어도 방식이 `none`이다. `mosaic`로 적으면 가리지 않은 영상을
가렸다고 납품하게 된다.

`deidentification`은 라벨링에 쓴 작업 사본의 처리 기록이다.
얼굴 수는 납품 항목이 아니므로 블록에 `face_count` 키가 없어야 하고, 내보내기는 얼굴 좌표 파일을 읽지 않는다.
"""
import json
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.exports.export_package import (
    build_metadata,
    write_package,
)
from httpx import AsyncClient
from jsonschema import Draft202012Validator
from reborn_contracts.clip_metadata import ClipMetadata
from tests import _storage
from tests._helpers import new_project, seed_clip_work, upload_video
from tests.exports.test_exports import (
    _NOW,
    _PAYLOAD,
    _approve,
    _built_package,
    _clip,
    _flush_pending,
    _video,
    _work,
)

_COORDS = {"bucket": "b1", "key": "transcoded/720p/x.faces.json",
           "schema_version": 1, "coordinate_space": "output_pixels"}


def _report(**kw) -> dict:
    """deid 서버 보고와 같은 모양의 `extra["deident"]`를 만든다. 인자로 값을 덮어쓴다."""
    base = {"model": "face-anonymizer", "masked": True, "review": [], "notices": [],
            "quality": "downscale", "replaced": "analysis-720p", "review_needed": False,
            "detection_rate": 0.7088}
    base.update(kw)
    return base


def _deid_video(status: str = "done", **report) -> Video:
    """비식별화 상태와 보고를 정한 영상 모델을 만든다."""
    v = _video(deident_status=status)
    v.extra = {**v.extra, "deident": _report(**report)}
    return v


def _deid(v: Video) -> dict:
    """클립 메타의 비식별화 블록을 JSON 모양으로 만든다."""
    m = build_metadata(_work(_PAYLOAD), v, _clip(start=10.0, end=20.0), _NOW, 0.0)
    return m.model_dump(mode="json")["deidentification"]


def test_done_with_face_model_is_processed_with_mosaic():
    """얼굴 모델로 비식별화가 끝난 영상이 처리됨·모자이크로 나가는지 확인한다."""
    d = _deid(_deid_video())
    assert d["processed"] is True
    assert d["method"] == "mosaic"
    assert d["suspected_miss"] is False


def test_not_masked_but_finished_is_processed_without_a_method():
    """가릴 얼굴이 없어 `masked`가 거짓인 영상은 처리됨이지만 방식은 `none`으로 나가는지 확인한다."""
    d = _deid(_deid_video(masked=False, review_needed=True))
    assert d == {"processed": True, "method": "none", "suspected_miss": True}


def test_essential_module_no_detection_report_does_not_claim_mosaic():
    """내장 모듈이 얼굴을 찾지 못했을 때 남기는 보고 그대로 방식이 `none`인지 확인한다."""
    v = _deid_video(model="essential-yunet", masked=False, review_needed=True,
                    review=["no-detections"], detected_frames=0, boxes=0,
                    detection_rate=0.0)
    d = _deid(v)
    assert d["method"] == "none", d
    assert d["processed"] is True and d["suspected_miss"] is True


def test_essential_module_with_detections_says_mosaic():
    """내장 모듈이 얼굴을 가린 영상은 그대로 `mosaic`로 나가는지 확인한다."""
    v = _deid_video(model="essential-yunet", masked=True, review_needed=False,
                    detected_frames=120, boxes=150, detection_rate=0.4)
    assert _deid(v) == {"processed": True, "method": "mosaic", "suspected_miss": False}


def test_passthrough_skipped_and_unfinished_are_not_processed():
    """검증용 복사·건너뜀·진행 중·실패 영상이 처리되지 않음(기본값)으로 나가는지 확인한다."""
    cases = [
        _deid_video(model="passthrough", masked=False),
        _deid_video(status="skipped"),
        _deid_video(status="pending"),
        _deid_video(status="failed"),
        _video(),  # 보고 자체가 없는 영상
    ]
    for v in cases:
        d = _deid(v)
        assert d == {"processed": False, "method": "none",
                     "suspected_miss": False}, v.deident_status


def test_review_needed_becomes_suspected_miss():
    """deid 보고의 `review_needed`가 참이면 사람이 다시 봐야 한다는 표시(`suspected_miss`)로 나가는지 확인한다."""
    assert _deid(_deid_video(review_needed=True))["suspected_miss"] is True


def test_block_has_no_face_count():
    """처리한 영상과 처리하지 않은 영상 모두 비식별화 블록에 얼굴 수 키가 없는지 확인한다."""
    for v in (_deid_video(coordinates=_COORDS), _deid_video(status="skipped"), _video()):
        d = _deid(v)
        assert "face_count" not in d
        assert set(d) == {"processed", "method", "suspected_miss"}


def test_package_counts_processed_videos(tmp_path: Path):
    """리포트가 실린 클립의 영상 수와 그중 비식별화한 영상 수를 적는지 확인한다."""
    with_coords = _deid_video(coordinates=_COORDS)
    without_coords = _deid_video()
    plain = _video(deident_status="skipped")
    rows = [(_work(_PAYLOAD), with_coords, _clip(start=10.0, end=20.0)),
            (_work(_PAYLOAD), without_coords, _clip()),
            (_work(_PAYLOAD), plain, _clip())]
    out = tmp_path / "p.zip"
    report = write_package(rows, {r[0].work_item_id: _NOW for r in rows}, out,
                           project_name="t")
    with ZipFile(out) as z:
        metas = {json.loads(z.read(n))["source_video_id"]: json.loads(z.read(n))
                 for n in z.namelist() if n.startswith("metadata/")}
        on_disk = json.loads(z.read("report.json"))

    assert metas[str(with_coords.video_id)]["deidentification"]["processed"] is True
    assert metas[str(without_coords.video_id)]["deidentification"]["processed"] is True
    assert metas[str(plain.video_id)]["deidentification"]["processed"] is False
    assert all("face_count" not in m["deidentification"] for m in metas.values())
    assert on_disk["deidentification"] == report["deidentification"] == {
        "videos": 3, "processed_videos": 2, "masked_videos": 2,
    }


def test_package_with_and_without_detections_passes_its_own_validation(tmp_path: Path):
    """얼굴을 찾은 영상과 찾지 못한 영상이 섞인 패키지가 자체 검증과 동봉 스키마를 통과하는지 확인한다.

    방식은 영상마다 사실대로 나가고, 리포트는 처리한 영상과 가린 영상을 따로 센다.
    """
    hit = _deid_video(model="essential-yunet", masked=True, review_needed=False)
    miss = _deid_video(model="essential-yunet", masked=False, review_needed=True,
                       review=["no-detections"])
    rows = [(_work(_PAYLOAD), hit, _clip(start=10.0, end=20.0)),
            (_work(_PAYLOAD), miss, _clip())]
    out = tmp_path / "p.zip"
    report = write_package(rows, {r[0].work_item_id: _NOW for r in rows}, out,
                           project_name="t")
    assert report["included"] == 2 and report["excluded"] == 0, report
    with ZipFile(out) as z:
        schema = json.loads(z.read("schema/clip-metadata-v1.json"))
        metas = [json.loads(z.read(n)) for n in z.namelist() if n.startswith("metadata/")]
        readme = z.read("README.md").decode()
    validator = Draft202012Validator(schema)
    for m in metas:
        assert list(validator.iter_errors(m)) == [], m
        ClipMetadata.model_validate(m)
    by_video = {m["source_video_id"]: m["deidentification"] for m in metas}
    assert by_video[str(hit.video_id)]["method"] == "mosaic"
    assert by_video[str(miss.video_id)] == {
        "processed": True, "method": "none", "suspected_miss": True}
    assert report["deidentification"] == {
        "videos": 2, "processed_videos": 2, "masked_videos": 1}
    # 받는 쪽이 `none`과 `masked_videos`의 뜻을 패키지 안에서 읽을 수 있다.
    assert "`masked_videos`" in readme and "`none`" in readme


def test_report_counts_nothing_when_no_video_was_processed(tmp_path: Path):
    """비식별화한 영상이 없으면 처리한 영상 수가 0인지 확인한다."""
    w = _work(_PAYLOAD)
    out = tmp_path / "p.zip"
    report = write_package([(w, _video(), _clip())], {w.work_item_id: _NOW}, out,
                           project_name="t")
    assert report["deidentification"] == {"videos": 1, "processed_videos": 0, "masked_videos": 0}


async def _mark_deidentified(video_id: str, coordinates: dict | None) -> None:
    """영상을 비식별화 완료 상태로 만든다. 좌표 기록을 주면 함께 남긴다."""
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(video_id))
        v.deident_status = "done"
        rep = _report()
        if coordinates:
            rep["coordinates"] = coordinates
        v.extra = {**(v.extra or {}), "deident": rep}
        await s.commit()


async def test_worker_exports_deidentification_without_reading_coordinates(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """워커가 좌표 기록이 있어도 좌표 파일을 읽지 않고 처리 여부와 방식만 싣는지 확인한다."""
    from api_gateway.services.media import deident as deident_service
    from api_gateway.services.media import storage

    reads: list[str] = []
    real_get = storage.get_object_body

    def counting_get(*, bucket: str, key: str):
        """좌표 파일을 읽은 횟수를 센다."""
        if key.endswith(".faces.json"):
            reads.append(key)
        return real_get(bucket=bucket, key=key)

    monkeypatch.setattr(storage, "get_object_body", counting_get)

    pid = await new_project(client, admin_token, "deidfaces")
    vid = await upload_video(client, admin_token, pid)
    c1, w1 = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    c2, w2 = await seed_clip_work(pid, vid, start=20.0, end=30.0)
    for w in (w1, w2):
        await _approve(w, _PAYLOAD)

    key = deident_service.coordinates_key(UUID(vid))
    _storage.put_bytes(key, b'{"frames":[]}', content_type="application/json")
    await _mark_deidentified(vid, {"bucket": _storage.BUCKET, "key": key,
                                   "schema_version": 1, "coordinate_space": "output_pixels"})

    eid = (await client.post(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()["export_id"]
    await _flush_pending()
    assert reads == []

    with await _built_package(eid) as z:
        blocks = [json.loads(z.read(f"metadata/{c}.json"))["deidentification"] for c in (c1, c2)]
        report = json.loads(z.read("report.json"))
    assert blocks == [{"processed": True, "method": "mosaic", "suspected_miss": False}] * 2
    assert report["deidentification"] == {"videos": 1, "processed_videos": 1, "masked_videos": 1}
    assert report["included"] == 2


def test_shipped_schema_explains_method_none():
    """동봉 스키마의 비식별화 설명이 가린 영역이 없을 때의 값을 알리는지 확인한다."""
    from reborn_contracts.clip_metadata import json_schema

    text = json_schema()["$defs"]["ClipDeidentification"]["description"]
    assert "method는 none" in text
