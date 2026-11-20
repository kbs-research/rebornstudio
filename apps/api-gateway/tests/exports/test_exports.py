"""Export 도메인 테스트. 납품 패키지 빌드, 목록, presigned download, 완료 목록을 확인한다."""
import json
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
import yaml
from api_gateway.database import SessionLocal
from api_gateway.models import Clip, Video, WorkItem
from api_gateway.services.exports.export_package import build_metadata, write_package
from api_gateway.services.exports.export_service import process_pending
from httpx import AsyncClient
from pydantic import ValidationError
from tests import _storage
from tests._helpers import add_member, make_user, new_project, seed_clip_work, upload_video


async def _flush_pending():
    """worker beat가 호출하는 process_pending을 즉시 실행한다.

    테스트에서 처리 시점을 맞추는 데 쓴다. beat 기본값은 한 건이라서 앞선 테스트가 남긴
    대기 건이 먼저 잡혀도 이 테스트의 건까지 만들도록 건수를 넉넉히 준다.
    """
    async with SessionLocal() as s:
        return await process_pending(s, limit=10)


async def test_create_empty_export(client: AsyncClient, admin_token: str):
    """생성 직후에는 'pending'이고 worker 처리 뒤 'completed'가 되는지 확인한다."""
    pid = await new_project(client, admin_token, "exp")
    r = await client.post(
        f"/projects/{pid}/exports",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    # worker 처리를 흉내 낸다
    n = await _flush_pending()
    assert n >= 1
    # download 엔드포인트로 완료를 확인한다.
    r2 = await client.get(
        f"/exports/{body['export_id']}/download",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r2.status_code == 200
    assert r2.json()["item_count"] == 0


async def test_list_exports(client: AsyncClient, admin_token: str):
    """내보내기 목록이 만든 내보내기를 모두 싣는지 확인한다."""
    pid = await new_project(client, admin_token, "exl")
    await client.post(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )
    await client.post(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )
    r = await client.get(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_export_download_url(client: AsyncClient, admin_token: str):
    """완료된 내보내기가 다운로드 URL을 주는지 확인한다."""
    pid = await new_project(client, admin_token, "exd")
    eid = (
        await client.post(
            f"/projects/{pid}/exports",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).json()["export_id"]
    await _flush_pending()  # pending을 completed로 처리한다
    r = await client.get(
        f"/exports/{eid}/download",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["download_url"].startswith("http")
    assert body["expires_in"] > 0
    assert body["item_count"] == 0


async def test_annotator_cannot_create_export(client: AsyncClient, admin_token: str):
    """annotator가 내보내기를 만들 수 없는지(403) 확인한다."""
    pid = await new_project(client, admin_token, "exa")
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.post(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {annot_tok}"}
    )
    assert r.status_code == 403


# 납품 패키지
#
# 패키지가 납품 기준이고, 매니페스트는 그 안의 색인이다.
# 색인에는 `clip_id`와 구간이 있고, 이미 지워진 원본을 가리키는 `video_uri`는 없다.

_PAYLOAD = {
    "caption": {
        "brief": "앵커가 서초동 집회 소식을 전하고 있습니다.",
        "brief_sentences": [
            {"id": "s1", "text": "앵커가 소식을 전한다.", "start": 12.5, "end": 20.0}
        ],
    },
    "meta": {"genre": "정치"},
    "tags": {"events": ["집회"]},
    "description": {"persons": ["앵커"], "location": "스튜디오", "actions": ["보도한다"]},
    "keywords": ["집회"],
    "speakers": [{"pid": "p1", "name": "앵커"}],
    "speaker_segments": [{"pid": "p1", "startMs": 0, "endMs": 3000}],
}
_NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _video(**kw) -> Video:
    """납품 필드를 채운 영상 모델을 만든다. 인자로 필드를 덮어쓴다."""
    base = dict(
        video_id=uuid4(), project_id=uuid4(), title="9시 뉴스",
        broadcaster="샘플 방송", program_name="9시 뉴스 샘플",
        aired_at=_NOW, duration_seconds=2366.7,
        source_uri="s3://bkt/intake/a.mp4", source_retained=True,
        extra={"probe": {"width": 1280, "height": 720, "fps": 30.0}},
    )
    base.update(kw)
    return Video(**base)


def _work(payload=None) -> WorkItem:
    """payload만 정한 작업 모델을 만든다."""
    return WorkItem(work_item_id=uuid4(), video_id=uuid4(), project_id=uuid4(), payload=payload)


def _clip(start=658.734, end=700.0, rights="auto_cleared") -> Clip:
    """정상 상태 클립 모델을 만든다. 승인까지 온 클립의 실제 상태가 정상이다."""
    return Clip(clip_id=uuid4(), start_seconds=start, end_seconds=end, rights_status=rights)


def test_metadata_carries_probe_resolution():
    """해상도·fps를 다시 재지 않고 probe가 추출해 둔 값을 쓰는지 확인한다.

    `Video`의 파이썬 속성은 `extra`다. DB 컬럼 이름만 `metadata`다(SQLAlchemy 예약어 회피).
    예약어를 그대로 읽으면 `MetaData` 객체가 잡혀 probe 값이 빈 값이 된다.
    """
    m = build_metadata(_work(_PAYLOAD), _video(), _clip(), _NOW, 0.0)
    assert m.resolution and m.resolution.width == 1280 and m.resolution.height == 720
    assert m.fps == 30.0


def test_metadata_carries_clip_range_on_source_axis():
    """클립 구간이 원본 시간 축 기준으로 실리는지 확인한다. 구간이 있어야 캡션이 설명하는 부분을 알 수 있다."""
    m = build_metadata(_work(_PAYLOAD), _video(), _clip(), _NOW, 0.0)
    assert m.start_time == 658.734 and m.end_time == 700.0
    assert m.duration == pytest.approx(41.266)
    assert m.source_start_offset == 0.0


def test_metadata_shifts_by_offset_when_analysis_read_a_master():
    """분석이 마스터를 읽었으면 원본 시간 축으로 환산하는지 확인한다.

    이 시스템의 인코딩은 원본 `start_time` 1.500s를 마스터에서 0.033s로 만든다.
    그 차이를 더하지 않으면 클립 시각이 고객 원본과 맞지 않는다.
    """
    m = build_metadata(_work(_PAYLOAD), _video(), _clip(), _NOW, 1.467)
    assert m.start_time == pytest.approx(660.201)
    assert m.source_start_offset == 1.467


def test_description_and_keywords_ship_empty():
    """세부 묘사와 키워드를 키는 두고 값은 비워 싣는지 확인한다. 이전 payload에 남은 값도 읽지 않는다."""
    m = build_metadata(_work(_PAYLOAD), _video(), _clip(), _NOW, 0.0)
    assert m.description.model_dump() == {
        "persons": [], "location": "", "actions": [], "events": [], "on_screen_text": [],
    }
    assert m.keywords == []
    doc = m.model_dump(mode="json")
    assert "description" in doc and "keywords" in doc, "패키지 형식은 그대로다"


def test_speakers_are_not_shipped():
    """화자 구간을 납품하지 않는지 확인한다.

    납품 스키마는 사람이 검토·편집한 것만 싣는다. 워크벤치에는 화자 편집 기능이 없어서 화자 구간을 실을 근거가 없다.
    MI 원본을 그대로 실으면 스택을 바꿀 때 모양이 달라지므로 스키마 형식으로 고정할 수 없다.
    """
    m = build_metadata(_work(_PAYLOAD), _video(), _clip(), _NOW, 0.0)
    assert m.speakers == []


def test_metadata_rejects_short_caption():
    """캡션이 없거나 너무 짧으면 검증에서 걸리는지 확인한다. 캡션이 학습 데이터의 핵심이다."""
    with pytest.raises(ValidationError):
        build_metadata(_work({"caption": {"brief": "짧다"}}), _video(), _clip(), _NOW, 0.0)


def test_package_excludes_with_reason_not_silently(tmp_path: Path):
    """싣지 못하는 클립을 버리지 않고 사유와 함께 남기는지 확인한다."""
    ok = (_work(_PAYLOAD), _video(), _clip())
    bad = (_work({"caption": {"brief": "짧다"}, "meta": {"genre": "정치"}}), _video(), _clip())
    approved = {ok[0].work_item_id: _NOW, bad[0].work_item_id: _NOW}
    out = tmp_path / "p.zip"
    report = write_package([ok, bad], approved, out, project_name="t")

    assert report["included"] == 1 and report["excluded"] == 1
    # 사유는 필드별로 모두 남긴다. 하나만 남기면 고치고 다시 돌렸을 때 또 걸린다.
    reasons = report["exclusions"][0]["reasons"]
    # 앞부분은 화면 이름이다. `caption:` 같은 스키마 키를 목록에 표시하지 않는다.
    assert any(r.startswith("캡션:") for r in reasons), reasons
    # 사유는 읽는 사람이 쓰는 용어로 쓴다.
    # pydantic 원문("String should have at least 10 characters")이나 스키마 키가 나가면
    # 한국어 화면의 빨간 배너에 영어가 표시된다. 그래서 이름 부분까지 확인한다.
    assert all(not re.search(r"[A-Za-z]{4,}", r) for r in reasons), reasons
    # 스키마 키는 버리지 않고 따로 싣는다. 받는 쪽이 스키마와 대응시키는 이름이다.
    assert "caption" in report["exclusions"][0]["fields"]

    with ZipFile(out) as z:
        names = set(z.namelist())
        assert "manifest.jsonl" in names
        assert "schema/clip-metadata-v1.json" in names
        assert "dataset_card.yaml" in names and "README.md" in names and "report.json" in names
        # 미디어는 담지 않는다. 고객이 원본을 이미 가지고 있다.
        assert not any(n.startswith("media/") for n in names)
        line = json.loads(z.read("manifest.jsonl").decode().strip())
        assert line["metadata_path"] in names
        # 체크섬은 항상 xxh64다. xxhash가 없을 때 다른 알고리즘으로 조용히 바꾸지 않는다.
        assert line["checksum"].startswith("xxh64:"), line["checksum"]
        # 색인 행마다 스키마 버전을 싣는다. 값의 기준은 `ClipMetadata`다.
        assert line["schema_version"] == "1.0"


def test_package_text_tells_the_truth_to_whoever_opens_it(tmp_path: Path):
    """README·dataset_card가 사실과 맞는지 확인한다. 두 파일은 받는 쪽이 가장 먼저 읽는다.

    낡은 설명은 바로 오해로 이어진다. 세 가지를 확인한다.

    1. README의 제외 사유 목록에 비식별화 신고가 있다.
       목록이 틀리면 클립이 빠진 이유를 찾는 사람이 틀린 사유를 읽는다.
    2. 대조 안내대로 하면 실제로 대조된다. 값의 `sha256:` 접두어를 떼라는 설명과
       macOS에도 있는 명령이 함께 있어야 한다.
    3. 카드의 라이선스가 무엇에 적용되지 않는지 밝힌다. `Apache-2.0` 하나만 쓰면
       방송 보도영상 메타데이터가 그 라이선스인 것처럼 읽힌다.
    """
    ok = (_work(_PAYLOAD), _video(), _clip())
    out = tmp_path / "p.zip"
    write_package([ok], {ok[0].work_item_id: _NOW}, out, project_name="t",
                  generated_at=_NOW)

    with ZipFile(out) as z:
        readme = z.read("README.md").decode()
        card = z.read("dataset_card.yaml").decode()

    # YAML로 읽혀야 한다. 확장자가 형식을 정하므로 읽히지 않으면 그 자체로 결함이다.
    # 주석이 있는 파일이라 고치다가 형식을 깨뜨리기 쉽다.
    parsed = yaml.safe_load(card)
    assert parsed["license"]["platform_source"] == "Apache-2.0"
    # 공정·보증 절은 싣지 않는다. 검수·비식별화 정보는 클립 레코드와 report.json에 있다.
    assert "processing" not in parsed and "quality" not in parsed, parsed

    # 제외 사유 세 가지가 모두 있어야 한다. 하나라도 빠지면 목록이 틀린 정보가 된다.
    assert "비식별화 신고: 제외" in readme and "비식별화 신고: 처리 대기" in readme, readme
    assert "필수값" in readme and "원본 축" in readme, readme
    # 접두어를 떼라는 안내와 두 OS의 명령이 함께 있어야 실제로 대조할 수 있다.
    assert "sha256:" in readme and "shasum -a 256" in readme, readme
    # 라이선스는 무엇에 적용되지 않는지까지 밝힌다.
    assert "platform_source: Apache-2.0" in card
    assert "dataset:" in card, card
    # 어느 전달분인지 표시한다. `version`을 고정하면 두 번째 전달분이 첫 번째와 구분되지 않는다.
    assert "generated_at:" in card and "version: 1" not in card, card


def test_package_excludes_rights_not_cleared(tmp_path: Path):
    """정상 상태 클립만 납품하는지 확인한다.

    승인된 라벨이라도 비식별화 신고로 멈췄거나 제외된 클립은 빠진다.
    `legal_review`는 비식별화 미흡 신고가 보내는 상태다.
    처리가 끝나지 않은 것을 내보내면 되돌릴 수 없다.
    """
    approved, rows = {}, []
    for st in ("legal_review", "rejected", "cleared"):
        w = _work(_PAYLOAD)
        approved[w.work_item_id] = _NOW
        rows.append((w, _video(), _clip(rights=st)))
    report = write_package(rows, approved, tmp_path / "p.zip", project_name="t")

    assert report["included"] == 1                      # cleared 하나만
    assert report["excluded"] == 2
    reasons = [e["reasons"][0] for e in report["exclusions"]]
    # 이름은 화면이 쓰는 용어다. 컬럼명 `rights_status`를 쓰지 않는다.
    assert sorted(reasons) == ["비식별화 신고: 제외", "비식별화 신고: 처리 대기"], reasons
    assert all(e["fields"] == ["rights_status"] for e in report["exclusions"])


def test_package_keeps_clips_without_source_meta(tmp_path: Path):
    """출처 메타가 비어도 납품하는지 확인한다. 납품 스키마의 필수 항목은 캡션뿐이다.

    있으면 넣고 없으면 빈 채로 둔다. `genre`·`aired_at`이 없다고 제외하지 않는다.
    """
    w = _work({"caption": {"brief": "앵커가 서초동 집회 소식을 전하고 있습니다."}})
    v = _video(broadcaster=None, program_name=None, aired_at=None, duration_seconds=None)
    report = write_package([(w, v, _clip())], {w.work_item_id: _NOW},
                           tmp_path / "p.zip", project_name="t")
    assert report["included"] == 1, report["exclusions"]


def test_package_keeps_clips_without_review_record(tmp_path: Path):
    """승인 시각 기록이 없어도 납품하는지 확인한다. 기록 누락은 클립 내용의 문제와 별개다."""
    w = _work(_PAYLOAD)
    report = write_package([(w, _video(), _clip())], {}, tmp_path / "p.zip", project_name="t")
    assert report["included"] == 1, report["exclusions"]


def test_package_excludes_when_offset_unmeasured(tmp_path: Path):
    """분석본을 읽었는데 오프셋 기록이 없으면 환산할 수 없어 제외하는지 확인한다.

    어긋난 시각을 조용히 내보내지 않는다.
    미디어를 납품하지 않으므로 시각이 유일한 위치 정보다.
    """
    v = _video(analysis_uri="s3://derived/x.mp4", extra={"probe": {}})
    w = _work(_PAYLOAD)
    report = write_package([(w, v, _clip())], {w.work_item_id: _NOW}, tmp_path / "p.zip",
                           project_name="t")
    assert report["included"] == 0
    # 컬럼명(`start_time`)과 괄호 설명 대신 화면에서 쓰는 용어로 적는다.
    assert report["exclusions"][0]["reasons"] == ["타임코드: 원본 축 보정값 없음"]
    assert report["exclusions"][0]["fields"] == ["source_start_offset"]


def test_package_report_has_no_fill_rates(tmp_path: Path):
    """리포트에 채움률이 없는지 확인한다. 라벨러가 채우는 선택 항목이 없어 잴 값이 없다."""
    full = (_work(_PAYLOAD), _video(), _clip())
    approved = {full[0].work_item_id: _NOW}
    report = write_package([full], approved, tmp_path / "p.zip", project_name="t")
    assert report["included"] == 1
    assert "fill_rates" not in report


def test_package_schema_comes_from_the_validator(tmp_path: Path):
    """패키지에 넣는 스키마가 검증기와 같은 출처에서 나오는지 확인한다. 출처가 다르면 둘이 서로 달라진다."""
    w = _work(_PAYLOAD)
    out = tmp_path / "p.zip"
    write_package([(w, _video(), _clip())], {w.work_item_id: _NOW}, out, project_name="t")
    with ZipFile(out) as z:
        schema = json.loads(z.read("schema/clip-metadata-v1.json"))
    assert schema["$id"].endswith("clip-metadata-v1.json")
    assert "caption" in schema["required"]
    # 세부 묘사와 키워드는 선택 항목이다.
    assert "description" not in schema["required"]
    assert "keywords" not in schema["required"]


# 완료 목록


async def _approve(work_item_id: str, payload: dict, *, with_review: bool = True) -> None:
    """작업을 승인 상태로 직접 바꾼다. 완료 목록과 패키지에 쓸 데이터를 만든다.

    `with_review`가 참이면 승인 판정 레코드도 함께 남긴다.
    패키지의 `review.reviewed_at`이 이 레코드에서 온다.
    레코드가 없어도 클립은 납품된다(`test_missing_approved_at_does_not_block_delivery`).
    검수 흐름 자체는 별도 테스트가 확인한다.
    """
    from api_gateway.models import Review, User
    from sqlalchemy import select

    async with SessionLocal() as s:
        wi = await s.get(WorkItem, UUID(work_item_id))
        wi.status = "approved"
        wi.payload = payload
        wi.submitted_at = datetime.now(UTC)
        # 등록 메타를 채운다. 실제 영상은 등록·MI 단계에서 채워진다.
        # 납품 스키마의 필수 항목은 캡션뿐이라 이 값이 없어도 제외되지 않는다.
        vid = await s.get(Video, wi.video_id)
        vid.broadcaster = vid.broadcaster or "샘플 방송"
        vid.program_name = vid.program_name or "9시 뉴스 샘플"
        vid.aired_at = vid.aired_at or datetime.now(UTC)
        vid.duration_seconds = vid.duration_seconds or 2366.7
        if with_review:
            reviewer = await s.scalar(select(User.user_id).limit(1))
            s.add(Review(work_item_id=wi.work_item_id, reviewer_id=reviewer, verdict="approve"))
        await s.commit()


async def test_completed_matches_export_set(client: AsyncClient, admin_token: str):
    """완료 목록의 행 집합이 내보내기와 같은지 확인한다.

    화면이 따로 세면 화면이 보여 준 것과 실제로 나가는 것이 달라진다.
    그 차이는 내보내기를 받아 보기 전에는 드러나지 않는다.
    """
    pid = await new_project(client, admin_token, "done1")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid, start=658.734, end=700.0)
    await _approve(wid, _PAYLOAD)

    r = await client.get(
        f"/projects/{pid}/completed", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["start_seconds"] == 658.734 and row["end_seconds"] == 700.0
    assert row["sentence_count"] == 1 and "timed_sentence_count" not in row

    # 같은 프로젝트를 내보내면 같은 집합이 나온다.
    # 패키지는 그중 검증 통과분만 담고, 담지 못한 것은 제외 사유로 남긴다.
    # 그래서 담긴 수는 total 이하일 수 있다.
    eid = (
        await client.post(
            f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
        )
    ).json()["export_id"]
    await _flush_pending()
    rows = (await client.get(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    assert rows[0]["status"] == "completed", rows[0]["error"]
    d = await client.get(
        f"/exports/{eid}/download", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert d.json()["item_count"] == body["total"]


async def test_completed_total_is_not_page_count(client: AsyncClient, admin_token: str):
    """`total`이 페이지 수가 아니라 전체 수인지 확인한다. 페이지에 실린 것만 세면 페이지를 넘길 때마다 총계가 달라진다."""
    pid = await new_project(client, admin_token, "done2")
    vid = await upload_video(client, admin_token, pid)
    for i in range(3):
        _c, w = await seed_clip_work(pid, vid, start=i * 10.0, end=i * 10.0 + 5.0)
        await _approve(w, _PAYLOAD)

    r = await client.get(
        f"/projects/{pid}/completed?limit=2",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


async def test_reviewer_can_read_completed(client: AsyncClient, admin_token: str):
    """검수자도 완료 목록을 볼 수 있는지 확인한다.

    완료본은 고객이 받아 갈 산출물이고 이 화면에는 편집 기능이 없다.
    """
    pid = await new_project(client, admin_token, "done3")
    _, rev_tok = await add_member(client, admin_token, pid, "reviewer")
    r = await client.get(
        f"/projects/{pid}/completed", headers={"Authorization": f"Bearer {rev_tok}"}
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0


async def test_non_member_cannot_read_completed(client: AsyncClient, admin_token: str):
    """멤버가 아니면 완료 목록을 볼 수 없는지 확인한다. 완료본은 프로젝트 산출물이다."""
    pid = await new_project(client, admin_token, "done4")
    _, outsider = await make_user(client, admin_token, "outsider")
    r = await client.get(
        f"/projects/{pid}/completed", headers={"Authorization": f"Bearer {outsider}"}
    )
    assert r.status_code == 403


async def test_completed_shows_rights_without_filtering(client: AsyncClient, admin_token: str):
    """완료 목록이 클립 상태로 거르지 않고 그대로 보여 주는지 확인한다.

    거르면 화면과 내보내기가 서로 달라진다.
    나가면 안 되는 것이 이 목록에 보이면 내보내기 쪽 결함이고, 그 사실이 화면에 드러나야 고칠 수 있다.
    """
    pid = await new_project(client, admin_token, "done5")
    vid = await upload_video(client, admin_token, pid)
    _c, w = await seed_clip_work(pid, vid, rights_status="rejected")
    await _approve(w, _PAYLOAD)
    r = await client.get(
        f"/projects/{pid}/completed", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["rights_status"] == "rejected"


# 원본 체크섬(미디어를 싣지 않는 대신 제공한다)


def test_metadata_carries_source_checksum():
    """원본 체크섬을 싣는지 확인한다.

    받는 쪽이 자기 파일이 이 시스템이 분석한 파일과 같은지 대조할 수 있다.
    """
    v = _video(source_sha256="sha256:abc123")
    m = build_metadata(_work(_PAYLOAD), v, _clip(), _NOW, 0.0)
    assert m.source_checksum == "sha256:abc123"


def test_metadata_without_checksum_still_exports():
    """아직 재지 않은 체크섬이 `null`로 나가고 납품을 막지 않는지 확인한다.

    체크섬은 받는 쪽의 대조를 돕는 값이라 납품 조건에 넣지 않는다.
    타임코드 오프셋은 없으면 값 자체가 틀리지만, 체크섬이 없다고 시각이 틀리지는 않는다.
    """
    m = build_metadata(_work(_PAYLOAD), _video(), _clip(), _NOW, 0.0)
    assert m.source_checksum is None


def test_package_reports_checksum_coverage(tmp_path: Path):
    """리포트가 대조 가능한 건수 비율을 세는지 확인한다. beat가 체크섬을 채우는 동안 비율이 오른다."""
    with_ck = (_work(_PAYLOAD), _video(source_sha256="sha256:a"), _clip())
    without = (_work(_PAYLOAD), _video(), _clip())
    approved = {with_ck[0].work_item_id: _NOW, without[0].work_item_id: _NOW}
    report = write_package([with_ck, without], approved, tmp_path / "p.zip", project_name="t")
    assert report["included"] == 2
    assert report["source_checksum_rate"] == 0.5


# 완료 상세(읽기 전용 확인 화면)


async def test_completed_detail_gives_everything_at_once(client: AsyncClient, admin_token: str):
    """완료 상세가 요약·라벨 내용·이력·납품 가능 여부를 한 번에 주는지 확인한다.

    세 번 요청하면 서로 다른 시점의 값이 섞인다.
    """
    pid = await new_project(client, admin_token, "det1")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid, start=658.734, end=700.0)
    await _approve(wid, _PAYLOAD)

    r = await client.get(
        f"/projects/{pid}/completed/{wid}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    d = r.json()
    assert d["row"]["start_seconds"] == 658.734
    assert d["caption"]["brief_sentences"][0]["start"] == 12.5
    assert "description" not in d and "keywords" not in d and "tags" not in d
    assert len(d["reviews"]) == 1 and d["reviews"][0]["verdict"] == "approve"


async def test_completed_detail_says_why_it_would_be_excluded(
    client: AsyncClient, admin_token: str
):
    """완료 상세가 그 클립이 납품에서 빠지는 이유를 알려 주는지 확인한다. report.json을 찾아보지 않아도 된다.

    판정은 패키지가 쓰는 관문과 같은 함수(`export_package.check_row`)로 한다.
    판정을 다시 구현하면 화면이 실린다고 표시한 클립이 실제로는 빠질 수 있다.
    그 차이는 패키지를 열어 보기 전에는 보이지 않는다.
    """
    pid = await new_project(client, admin_token, "det2")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    await _approve(wid, {"caption": {"brief": "짧다"}})   # 캡션 미달

    d = (await client.get(
        f"/projects/{pid}/completed/{wid}", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    # 판정 결과는 `row`에 있다. 목록과 상세가 같은 필드를 읽어야 둘이 달라지지 않는다.
    b = d["row"]
    assert b["deliverable"] is False
    assert any(x.startswith("캡션:") for x in b["blockers"]), b["blockers"]
    # 화면에 그대로 표시되는 문장이라서 영어가 나가면 안 된다(write_package 쪽과 같은 검사).
    # 이름 부분도 확인한다. 스키마 키(`caption`)를 그대로 표시하지 않는다.
    assert all(not re.search(r"[A-Za-z]{4,}", x) for x in b["blockers"]), b["blockers"]


async def test_completed_list_says_which_rows_would_be_excluded(
    client: AsyncClient, admin_token: str
):
    """목록에서 먼저 납품 제외 여부를 보여 주는지 확인한다.

    전체 집계도 함께 준다. 페이지에 실린 것만 세면 '없음'이 틀린 정보가 된다.
    상세 화면에서만 알려 주면 납품 결과 `0건`을 보고서야 처음 알게 된다.
    """
    pid = await new_project(client, admin_token, "declist")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=60.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=60.0, end=120.0)
    await _approve(w1, _PAYLOAD)                  # 실린다
    await _approve(w2, {"caption": {"brief": "짧다"}})    # 캡션 미달

    page = (await client.get(
        f"/projects/{pid}/completed", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    by_id = {r["work_item_id"]: r for r in page["items"]}
    assert by_id[str(w1)]["deliverable"] is True and by_id[str(w1)]["blockers"] == []
    assert by_id[str(w2)]["deliverable"] is False
    assert any(b.startswith("캡션:") for b in by_id[str(w2)]["blockers"])
    assert page["total"] == 2
    assert page["deliverable_total"] == 1     # 페이지와 관계없이 전체 기준으로 센다.


async def test_deliverable_total_is_none_not_zero_when_not_counted(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """스캔 상한을 넘으면 0이 아니라 `None`인지 확인한다.

    0으로 주면 하나도 나가지 못한다는 틀린 정보가 된다.
    """
    from api_gateway.services.exports import export_service

    pid = await new_project(client, admin_token, "declimit")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid)
    await _approve(w1, _PAYLOAD)

    monkeypatch.setattr(export_service, "DELIVERABLE_SCAN_LIMIT", 0)
    page = (await client.get(
        f"/projects/{pid}/completed", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    assert page["total"] == 1
    assert page["deliverable_total"] is None
    # 세지 않은 이유를 화면이 표시할 수 있게 상한을 함께 준다.
    assert page["deliverable_scan_limit"] == 0


async def test_rights_gate_is_the_same_on_screen_and_in_the_package(
    client: AsyncClient, admin_token: str
):
    """권리 관문이 화면에도 적용되는지 확인한다. 화면에 없으면 화면은 납품 대상이라고 표시하는데 패키지는 뺀다.

    관문을 여러 곳에 따로 두면 화면 쪽이 `rights_blocker`를 빠뜨려 완료 목록이 신고로 멈춘 클립을 납품 대상으로 표시한다.
    그래서 관문을 `check_row` 하나로 둔다.
    """
    pid = await new_project(client, admin_token, "rightsgate")
    vid = await upload_video(client, admin_token, pid)
    cid, wid = await seed_clip_work(pid, vid)
    await _approve(wid, _PAYLOAD)                  # 내용에는 문제가 없다

    async with SessionLocal() as s:
        c = await s.get(Clip, cid)
        c.rights_status = "legal_review"                  # 비식별화 신고로 멈춤
        await s.commit()

    page = (await client.get(
        f"/projects/{pid}/completed", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    row = page["items"][0]
    assert row["deliverable"] is False
    assert row["blockers"] == ["비식별화 신고: 처리 대기"], row["blockers"]
    assert page["deliverable_total"] == 0


async def test_missing_approved_at_does_not_block_delivery(
    client: AsyncClient, admin_token: str
):
    """승인 시각이 없다고 납품을 막지 않는지 확인한다. 납품 스키마의 필수 항목은 캡션뿐이다.

    패키지는 막지 않으므로, 화면이 막으면 실제로 실리는 클립을 실리지 않는다고 표시한다.
    권리 관문과 방향만 반대인 같은 불일치라서 함께 테스트로 보장한다.
    """
    pid = await new_project(client, admin_token, "noapprovedat")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    # 판정 레코드 없이 승인한다. 옛 데이터와 수기 전이가 이런 형태다.
    await _approve(wid, _PAYLOAD, with_review=False)

    page = (await client.get(
        f"/projects/{pid}/completed", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    assert page["items"][0]["approved_at"] is None
    assert page["items"][0]["deliverable"] is True, page["items"][0]["blockers"]


async def test_completed_detail_rejects_items_that_are_not_completed(
    client: AsyncClient, admin_token: str
):
    """승인된 건이 아니면 완료 상세가 404인지 확인한다. 완료 상세에는 승인된 건만 나온다."""
    pid = await new_project(client, admin_token, "det3")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)      # available 상태 그대로
    r = await client.get(
        f"/projects/{pid}/completed/{wid}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 404


async def test_reviewer_can_read_completed_detail(client: AsyncClient, admin_token: str):
    """검수자도 완료 상세를 열 수 있는지 확인한다. 완료본은 확인용이고 이 화면에는 편집 기능이 없다."""
    pid = await new_project(client, admin_token, "det4")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    await _approve(wid, _PAYLOAD)
    _, rev_tok = await add_member(client, admin_token, pid, "reviewer")
    r = await client.get(
        f"/projects/{pid}/completed/{wid}", headers={"Authorization": f"Bearer {rev_tok}"}
    )
    assert r.status_code == 200


# 검수용 샘플


def test_sample_spreads_across_the_whole_set():
    """샘플이 앞에서부터 자르지 않고 전체에 고르게 퍼지는지 확인한다. 앞부터 자르면 한 영상·한 프로그램에 몰린다.

    정렬이 제출 순이라 앞 n건은 데이터의 일부 구간일 뿐 전체 형식을 대표하지 않는다.
    난수도 쓰지 않는다. 같은 입력에 같은 샘플이 나와야 같은 샘플을 다시 달라는 요청에 응할 수 있다.
    """
    from api_gateway.services.exports.export_service import _spread

    rows = list(range(100))
    got = _spread(rows, 5)
    assert got == [0, 20, 40, 60, 80]
    assert _spread(rows, 5) == got                    # 같은 입력이면 같은 샘플
    assert _spread(rows, 200) == rows                 # 모집단보다 크면 전량
    assert _spread(rows, 0) == rows


async def test_sample_package_says_it_is_a_sample(client: AsyncClient, admin_token: str):
    """이전에 요청한 샘플 내보내기를 워커가 샘플로 마저 만드는지 확인한다.

    새 요청은 샘플을 받지 않는다(`?sample`은 무시되고 전량이다).
    이미 큐에 있는 샘플 행은 같은 형식으로 끝내야 샘플을 납품본으로 오해하지 않는다.
    """
    from api_gateway.models import Export

    pid = await new_project(client, admin_token, "smp")
    vid = await upload_video(client, admin_token, pid)
    for i in range(3):
        _c, w = await seed_clip_work(pid, vid, start=i * 10.0, end=i * 10.0 + 5.0)
        await _approve(w, _PAYLOAD)

    auth = {"Authorization": f"Bearer {admin_token}"}
    full = await client.post(f"/projects/{pid}/exports?sample=2", headers=auth)
    assert full.status_code == 201
    assert full.json()["sample_size"] is None, "새 요청은 언제나 전량이다"
    me = (await client.get("/auth/me", headers=auth)).json()["user_id"]
    async with SessionLocal() as s:
        s.add(Export(project_id=UUID(pid), created_by=UUID(me), status="pending", sample_size=2))
        await s.commit()
    await _flush_pending()

    rows = (await client.get(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    sample = next(r for r in rows if r["sample_size"] == 2)
    assert sample["status"] == "completed", sample["error"]
    assert sample["item_count"] == 2          # 3건 중 2건만
    full_row = next(r for r in rows if r["export_id"] == full.json()["export_id"])
    assert full_row["item_count"] == 3


async def test_full_export_is_not_marked_as_sample(client: AsyncClient, admin_token: str):
    """전량 내보내기에 `sample_size`가 없는지 확인한다. 이전 export는 모두 전량이라 백필도 필요 없다."""
    pid = await new_project(client, admin_token, "smp2")
    r = await client.post(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.json()["sample_size"] is None


async def test_completed_search_looks_at_every_row_not_just_the_page(
    client: AsyncClient, admin_token: str
):
    """검색이 서버에서 전체 행을 대상으로 하는지 확인한다.

    페이지에 실린 행만 뒤지면 '없음'이 틀린 결과가 된다.
    페이지 크기를 1로 줄여도 2페이지에 있는 행을 찾아야 한다.
    """
    pid = await new_project(client, admin_token, "csearch")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    await _approve(w1, {**_PAYLOAD, "caption": {"brief": "앵커가 서초동 집회 소식을 전한다."}})
    await _approve(w2, {**_PAYLOAD, "caption": {"brief": "기자가 인천공항 고속도로를 보도한다."}})

    async def search(q: str, **params) -> dict:
        """이 검색어로 완료 목록을 조회한다."""
        r = await client.get(
            f"/projects/{pid}/completed",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"q": q, **params},
        )
        assert r.status_code == 200, r.text
        return r.json()

    hit = await search("인천공항", limit=1)
    assert hit["total"] == 1
    assert hit["items"][0]["work_item_id"] == str(w2)
    # 검색어가 맞지 않으면 0건을 돌려준다. 페이지 밖이라 찾지 못한 경우와 구분되어야 한다.
    assert (await search("없는말"))["total"] == 0


async def test_completed_search_still_reads_legacy_string_captions(
    client: AsyncClient, admin_token: str
):
    """`caption`이 문자열이던 옛 payload도 찾는지 확인한다.

    한쪽 모양만 보면 옛 데이터가 검색에서 조용히 빠지고, 빠진 행은 셀 수도 없다.
    """
    pid = await new_project(client, admin_token, "clegacy")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    await _approve(wid, {"caption": "옛 형식 캡션 — 스튜디오 오프닝"})

    r = await client.get(
        f"/projects/{pid}/completed",
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"q": "스튜디오 오프닝"},
    )
    assert r.json()["total"] == 1


async def test_deliverable_total_ignores_the_search(client: AsyncClient, admin_token: str):
    """납품 가능 집계가 검색과 관계없이 전체 기준인지 확인한다.

    검색 결과 안에서 세면 상단의 수가 검색어를 입력할 때마다 바뀐다.
    그러면 납품 가능한 전체 수를 알 수 없다.
    """
    pid = await new_project(client, admin_token, "cdel")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    await _approve(w1, _PAYLOAD)
    await _approve(w2, _PAYLOAD)

    body = (await client.get(
        f"/projects/{pid}/completed",
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"q": "없는말"},
    )).json()
    assert body["total"] == 0                 # 검색 결과는 0건
    assert body["deliverable_total"] == 2     # 그래도 납품 가능한 것은 2건이다


# 완료 목록 거르기


async def test_completed_filter_looks_at_every_row_not_just_the_page(
    client: AsyncClient, admin_token: str
):
    """필터가 서버에서 전체 행을 대상으로 적용되는지 확인한다. 검색과 같은 이유다.

    화면이 거르면 불러온 50건 안에서만 걸러져 `제외 없음`이 틀린 결과가 된다.
    페이지 크기를 1로 줄이면 클라이언트 필터는 첫 행만 본다.
    그 첫 행은 막히지 않은 행이라서, 화면에서 걸렀다면 0건이 나온다.
    """
    pid = await new_project(client, admin_token, "cfilter")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    await _approve(w1, _PAYLOAD)                          # 실린다(먼저 제출해서 1페이지)
    await _approve(w2, {"caption": {"brief": "짧다"}})     # 캡션 미달로 막힌다

    async def page(**params) -> dict:
        """한 행짜리 페이지로 완료 목록을 조회한다."""
        r = await client.get(
            f"/projects/{pid}/completed",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"limit": 1, **params},
        )
        assert r.status_code == 200, r.text
        return r.json()

    blocked = await page(filter="excluded")
    assert blocked["total"] == 1
    assert [r["work_item_id"] for r in blocked["items"]] == [str(w2)]

    ok = await page(filter="deliverable")
    assert ok["total"] == 1
    assert [r["work_item_id"] for r in ok["items"]] == [str(w1)]

    # 필터를 주지 않으면 두 건 모두 나온다.
    assert (await page())["total"] == 2


async def test_completed_filter_uses_the_same_gate_as_the_package(
    client: AsyncClient, admin_token: str
):
    """필터 판정이 패키지가 쓰는 관문을 그대로 쓰는지 확인한다. 규칙을 다시 구현하지 않는다.

    비식별화 신고로 멈춘 클립은 캡션에 문제가 없어도 막힌다.
    화면이 규칙을 따로 구현했다면 이 행이 `납품 대상`에 잡힌다(`check_row` docstring).
    """
    pid = await new_project(client, admin_token, "cfgate")
    vid = await upload_video(client, admin_token, pid)
    cid, wid = await seed_clip_work(pid, vid)
    await _approve(wid, _PAYLOAD)
    async with SessionLocal() as s:
        c = await s.get(Clip, UUID(cid))
        c.rights_status = "legal_review"                  # 비식별화 신고로 멈춤
        await s.commit()

    async def page(f: str) -> dict:
        """이 필터로 완료 목록을 조회한다."""
        return (await client.get(
            f"/projects/{pid}/completed",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"filter": f},
        )).json()

    assert (await page("deliverable"))["total"] == 0
    excluded = await page("excluded")
    assert excluded["total"] == 1
    assert any(b.startswith("비식별화 신고:") for b in excluded["items"][0]["blockers"])


async def test_completed_filter_says_when_the_gate_scan_was_cut(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """관문 스캔이 상한에서 끊기면 끊겼다고 알리는지 확인한다. 숨기면 `없음`이 틀린 결과가 된다.

    `filter_scanned < filter_candidates`가 끊긴 신호다. 화면은 이 두 값으로 분모를 표시한다.
    """
    from api_gateway.services.exports import export_service

    pid = await new_project(client, admin_token, "cfcut")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    await _approve(w1, _PAYLOAD)
    await _approve(w2, _PAYLOAD)

    monkeypatch.setattr(export_service, "DELIVERABLE_SCAN_LIMIT", 1)
    body = (await client.get(
        f"/projects/{pid}/completed",
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"filter": "deliverable"},
    )).json()
    assert body["filter_scanned"] == 1        # 한 행만 관문을 거쳤다
    assert body["filter_candidates"] == 2     # 후보는 두 행이었다
    assert body["total"] == 1                 # 그래서 이 필터 기준의 전체 수도 1이다


async def test_completed_filter_scan_fields_are_none_when_nothing_could_be_cut(
    client: AsyncClient, admin_token: str
):
    """SQL이 거르는 필터(`all`·`qc_rules`)의 스캔 필드가 `None`인지 확인한다. 이 필터는 스캔이 끊기지 않는다.

    0으로 채우면 화면이 하나도 확인하지 않은 것으로 읽는다(`deliverable_total`과 같은 문제).
    """
    pid = await new_project(client, admin_token, "cfnone")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    await _approve(wid, _PAYLOAD)

    for f in ("all", "qc_rules"):
        body = (await client.get(
            f"/projects/{pid}/completed",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"filter": f},
        )).json()
        assert body["filter_scanned"] is None, f
        assert body["filter_candidates"] is None, f


async def test_qc_rules_filter_and_the_warning_count_cannot_diverge(
    client: AsyncClient, admin_token: str
):
    """`규칙만 검사`의 수와 목록이 같은 조건식에서 나오는지 확인한다(판정 규칙은 한 곳에만 둔다).

    버튼 옆 경고는 1건인데 눌러 보면 2행인 화면이 되지 않게 한다.
    그래서 `qc_unreviewed_total`과 `filter=qc_rules`의 전체 수가 같아야 한다.
    마지막 승인 실행만 보는 것과 검사가 돌지 않은 건(`null`)을 세지 않는 것도 함께 확인한다.
    """
    from api_gateway.models import CaptionQcRun

    pid = await new_project(client, admin_token, "cfqc")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    _c3, w3 = await seed_clip_work(pid, vid, start=20.0, end=30.0)
    for w in (w1, w2, w3):
        await _approve(w, _PAYLOAD)

    async with SessionLocal() as s:
        # w1은 승인 실행에서 규칙만 돌았다. 이 한 건만 세어야 한다.
        s.add(CaptionQcRun(work_item_id=UUID(w1), trigger="approve", verdict="pass",
                           findings=[], adapter_key="rules"))
        # w2는 모델이 검사했다. 규칙만 돈 제출 실행은 승인 실행이 아니므로 세지 않는다.
        s.add(CaptionQcRun(work_item_id=UUID(w2), trigger="submit", verdict="pass",
                           findings=[], adapter_key="rules"))
        s.add(CaptionQcRun(work_item_id=UUID(w2), trigger="approve", verdict="pass",
                           findings=[], adapter_key="dummy-llm", model="m"))
        # w3은 검사가 아예 돌지 않았다. 모델 미검사와 다른 상태라서 세지 않는다.
        await s.commit()

    h = {"Authorization": f"Bearer {admin_token}"}
    summary = (await client.get(f"/projects/{pid}/completed/summary", headers=h)).json()
    listed = (await client.get(
        f"/projects/{pid}/completed", headers=h, params={"filter": "qc_rules"},
    )).json()

    assert summary["qc_unreviewed_total"] == 1
    assert listed["total"] == summary["qc_unreviewed_total"]
    assert [r["work_item_id"] for r in listed["items"]] == [str(w1)]

    # 표의 `자동 검수` 열도 같은 값이어야 한다.
    # 열은 `모델`인데 `규칙만 검사` 필터에 잡히면 같은 행이 서로 다른 값을 보인다.
    # `created_at` 기본값이 `now()`라서 한 트랜잭션에서 쓴 제출·승인 실행은 같은 시각이다.
    # 다시 찾는 join이 시각만 보면 제출 실행이 섞여 값이 달라진다.
    by_id = {r["work_item_id"]: r for r in (await client.get(
        f"/projects/{pid}/completed", headers=h,
    )).json()["items"]}
    assert by_id[str(w1)]["qc_checked_by"] == "rules"
    assert by_id[str(w2)]["qc_checked_by"] == "model"
    assert by_id[str(w3)]["qc_checked_by"] is None


async def test_completed_filter_keeps_the_head_counts_fixed(
    client: AsyncClient, admin_token: str
):
    """상단의 전체 집계가 고른 필터와 관계없는지 확인한다. 분모가 필터에 따라 바뀌면 안 된다.

    `total`은 거른 뒤의 수지만 `deliverable_total`은 항상 전체 기준이다.
    둘이 함께 바뀌면 납품 가능한 전체 수가 필터를 누를 때마다 바뀐다.
    """
    pid = await new_project(client, admin_token, "cfhead")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    await _approve(w1, _PAYLOAD)
    await _approve(w2, {"caption": {"brief": "짧다"}})

    for f, expect_total in (("all", 2), ("deliverable", 1), ("excluded", 1)):
        body = (await client.get(
            f"/projects/{pid}/completed",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"filter": f},
        )).json()
        assert body["total"] == expect_total, f
        assert body["deliverable_total"] == 1, f      # 필터가 바뀌어도 전체 기준 값은 그대로다
        assert body["qc_held_total"] == 0, f


async def test_completed_pages_do_not_repeat_or_skip_rows(
    client: AsyncClient, admin_token: str
):
    """페이지를 넘겨도 행이 겹치거나 빠지지 않는지 확인한다. `submitted_at`이 모두 비어 있어도 같다.

    `ORDER BY submitted_at` 하나로는 질의마다 순서가 달라진다.
    그러면 2페이지에 1페이지 행이 다시 나오고 어떤 행은 끝내 나오지 않는다.
    필터는 파이썬에서 페이지를 자르므로 이 문제가 더 크게 드러난다.
    수기 전이와 옛 데이터는 `submitted_at`이 NULL이라 이 조건이 실제로 생긴다.
    """
    pid = await new_project(client, admin_token, "cpage")
    vid = await upload_video(client, admin_token, pid)
    ids = []
    for i in range(6):
        _c, w = await seed_clip_work(pid, vid, start=i * 10.0, end=i * 10.0 + 10.0)
        await _approve(w, _PAYLOAD)
        ids.append(w)
    async with SessionLocal() as s:            # 제출 시각을 모두 비운다. 옛 데이터와 같은 조건이다.
        for w in ids:
            (await s.get(WorkItem, UUID(w))).submitted_at = None
        await s.commit()

    async def collect(f: str) -> list[str]:
        """이 필터로 두 페이지를 넘기며 작업 ID를 모은다."""
        out: list[str] = []
        for off in (0, 3):
            body = (await client.get(
                f"/projects/{pid}/completed",
                headers={"Authorization": f"Bearer {admin_token}"},
                params={"limit": 3, "offset": off, "summary": "false", "filter": f},
            )).json()
            out += [r["work_item_id"] for r in body["items"]]
        return out

    for f in ("all", "deliverable"):
        seen = await collect(f)
        assert len(seen) == 6, f                    # 빠진 것이 없다
        assert len(set(seen)) == 6, f               # 겹친 것도 없다
        assert set(seen) == {str(w) for w in ids}, f


async def test_export_history_says_who_made_it(client: AsyncClient, admin_token: str):
    """이력 행에 만든 사람의 이름이 있는지 확인한다.

    UUID만 주면 화면이 사람 이름을 표시할 수 없다. 같은 날 두 번 나간 패키지를 구별하는 기준이다.
    """
    pid = await new_project(client, admin_token, "expwho")
    await client.post(f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"})
    rows = (await client.get(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    assert rows[0]["created_by_name"]
    assert isinstance(rows[0]["created_by_name"], str)


async def test_deleted_video_still_exports_its_approved_work(
    client: AsyncClient, admin_token: str
):
    """삭제된 영상의 승인분도 납품하는지 확인한다.

    삭제는 오브젝트를 지우지 않으므로(`soft_delete`는 `deleted_at`만 기록한다) 패키지가 가리키는 대상이 남아 있다.
    걸러 내면 사람이 만든 라벨링 결과가 목록 정리 한 번으로 납품에서 빠진다.
    `deleted_at` 필터를 넣는 변경은 수정처럼 보이므로 이 테스트로 막는다.
    """
    from datetime import UTC, datetime

    pid = await new_project(client, admin_token, "delexp")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    await _approve(wid, _PAYLOAD)

    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.deleted_at = datetime.now(UTC)
        await s.commit()

    body = (await client.get(
        f"/projects/{pid}/completed",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert body["total"] == 1, "삭제된 영상의 승인분이 사라졌다"
    assert body["items"][0]["deliverable"] is True


# 캡션 품질검수 게이트(0074)


async def _set_qc(work_item_id: str, qc_status: str) -> None:
    """QC 결과를 직접 넣는다. 엔진은 다루지 않고 게이트만 확인한다."""
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, UUID(work_item_id))
        wi.qc_status = qc_status
        await s.commit()


async def test_completed_detail_names_qc_hold(client: AsyncClient, admin_token: str):
    """품질검수가 보류한 승인분의 완료 상세가 보류 코드(`qc_held`)를 돌려주는지 확인한다.

    승인된 건에 `아직 완료된 작업이 아닙니다`라고 표시하면 이미 승인한 사람이 남은 일을 알 수 없다.
    보류가 원인이면 다른 코드(`qc_held`)로 알려 화면이 보류 목록으로 안내하게 한다.
    """
    pid = await new_project(client, admin_token, "detqchold")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    await _approve(wid, _PAYLOAD)
    await _set_qc(wid, "held")
    r = await client.get(
        f"/projects/{pid}/completed/{wid}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "qc_held"


async def test_qc_pending_still_exports(client: AsyncClient, admin_token: str):
    """QC가 아직 돌지 않은 건을 납품하는지 확인한다. 게이트를 `== 'passed'`로 짜면 안 되는 이유다.

    QC 컬럼이 생기기 전에 승인된 건도 기본값이 `pending`이다.
    `passed`만 통과시키면 그 승인분이 모두 납품에서 빠지고, 검사 엔진이 멈추면 0건이 나간다.
    """
    pid = await new_project(client, admin_token, "qcpend")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid, start=10.0, end=40.0)
    await _approve(wid, _PAYLOAD)

    async with SessionLocal() as s:
        assert (await s.get(WorkItem, UUID(wid))).qc_status == "pending"  # 기본값

    r = await client.get(
        f"/projects/{pid}/completed", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.json()["total"] == 1


async def test_qc_held_is_excluded(client: AsyncClient, admin_token: str):
    """보류된 건이 완료 목록과 내보내기에서 함께 빠지고 통과로 바뀌면 둘 다에 다시 들어오는지 확인한다."""
    pid = await new_project(client, admin_token, "qchold")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid, start=10.0, end=40.0)
    await _approve(wid, _PAYLOAD)
    await _set_qc(wid, "held")

    h = {"Authorization": f"Bearer {admin_token}"}

    async def _export_count() -> int:
        """내보내기를 하나 만들어 처리하고 이력 행의 담긴 수를 돌려준다."""
        eid = (await client.post(f"/projects/{pid}/exports", headers=h)).json()["export_id"]
        await _flush_pending()
        rows = (await client.get(f"/projects/{pid}/exports", headers=h)).json()
        row = next(x for x in rows if x["export_id"] == eid)
        assert row["status"] == "completed", row
        return row["item_count"]

    r = await client.get(f"/projects/{pid}/completed", headers=h)
    assert r.json()["total"] == 0
    assert await _export_count() == 0

    await _set_qc(wid, "passed")
    r2 = await client.get(f"/projects/{pid}/completed", headers=h)
    assert r2.json()["total"] == 1
    assert await _export_count() == 1


async def _built_package(export_id: str) -> ZipFile:
    """만들어진 패키지를 버킷에서 그대로 꺼낸다. 받는 쪽이 여는 바로 그 파일이다.

    `write_package`를 직접 부르는 테스트와 달리 후보 질의·샘플·업로드를 모두 거친 결과를 본다.
    보류분은 관문이 아니라 후보 단계에서 빠지므로 이 경로로만 리포트 누락을 잡는다.
    """
    from api_gateway.models import Export
    from api_gateway.services.media import storage

    async with SessionLocal() as s:
        key = (await s.get(Export, UUID(export_id))).manifest_s3_key
    return ZipFile(BytesIO(storage.get_object_body(bucket=_storage.BUCKET, key=key).read()))


async def test_package_report_counts_qc_held_as_excluded(
    client: AsyncClient, admin_token: str
):
    """보류분이 후보에서 빠지지만 리포트에는 사유와 함께 실리는지 확인한다.

    앱 화면에는 보류가 보이지만, 패키지만 받는 쪽에게는 리포트가 유일한 정보다.
    리포트의 분모에서 보류분이 빠지면 받는 쪽은 빠진 건이 있는지 알 수 없다.
    후보 질의가 `qc_status != 'held'`로 좁히고 `excluded`가 관문에 걸린 것만 세면 보류분이 있어도 `excluded: 0`으로 기록된다.
    """
    pid = await new_project(client, admin_token, "qcrep")
    vid = await upload_video(client, admin_token, pid)
    _c1, ok = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, held = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    await _approve(ok, _PAYLOAD)
    await _approve(held, _PAYLOAD)
    await _set_qc(held, "held")

    eid = (await client.post(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()["export_id"]
    await _flush_pending()

    with await _built_package(eid) as z:
        report = json.loads(z.read("report.json"))
        card = yaml.safe_load(z.read("dataset_card.yaml").decode())
        readme = z.read("README.md").decode()
        # 보류분은 담기지 않는다. 이 테스트가 확인하는 것은 게이트가 아니라 리포트다.
        assert f"metadata/{_c2}.json" not in z.namelist()
        assert f"metadata/{_c1}.json" in z.namelist()

    assert report["included"] == 1
    assert report["excluded"] == 1, report["exclusions"]
    # 이름 부분은 화면이 쓰는 용어 그대로다(사이드바 `품질검수`, 보류함 `보류`).
    assert report["excluded_by_reason"] == {"품질검수": 1}
    entry = report["exclusions"][0]
    assert entry["work_item_id"] == held
    assert entry["reasons"] == ["품질검수: 보류"]
    # 보류는 스키마 항목과 무관한 사유라서 `fields`를 빈 배열로 둔다. 키는 항상 있다.
    assert entry["fields"] == []
    # 한 패키지 안의 두 파일이 같은 분모를 쓴다. 카드는 이 차이를 제외분이라고 적는다.
    assert card["source_rows"] - card["total_clips"] == report["excluded"]
    # 사유를 읽는 사람을 위해 그 용어의 뜻도 같은 zip 안에 둔다.
    assert "품질검수 보류" in readme


async def test_sample_report_does_not_mix_the_project_total_into_the_sample(
    client: AsyncClient, admin_token: str
):
    """이전에 큐에 넣은 샘플의 집계가 표본 기준이고 프로젝트 전체 기준인 보류 건수를 섞지 않는지 확인한다.

    샘플에서는 `included + excluded`가 표본 크기다.
    여기에 프로젝트 전체의 보류 수를 더하면 한 수 안에 분모가 둘이 되어 `2건 중 3건 제외` 같은 문장이 나온다.
    전체 기준 집계가 어디 있는지는 샘플 README가 안내한다.
    """
    pid = await new_project(client, admin_token, "qcrepsmp")
    vid = await upload_video(client, admin_token, pid)
    for i in range(2):
        _c, w = await seed_clip_work(pid, vid, start=i * 10.0, end=i * 10.0 + 5.0)
        await _approve(w, _PAYLOAD)
    _c3, held = await seed_clip_work(pid, vid, start=30.0, end=35.0)
    await _approve(held, _PAYLOAD)
    await _set_qc(held, "held")

    from api_gateway.models import Export

    me = (await client.get("/auth/me", headers={"Authorization": f"Bearer {admin_token}"})).json()
    async with SessionLocal() as s:
        row = Export(project_id=UUID(pid), created_by=UUID(me["user_id"]), status="pending",
                     sample_size=1)
        s.add(row)
        await s.commit()
        eid = str(row.export_id)
    await _flush_pending()

    with await _built_package(eid) as z:
        report = json.loads(z.read("report.json"))
        readme = z.read("README.md").decode()

    assert report["sample"] is True and report["sampled_from"] == 2   # 후보 전체 수
    assert report["included"] == 1 and report["excluded"] == 0
    assert report["excluded_by_reason"] == {}
    # 샘플 README가 전체 집계를 볼 곳을 안내한다. 여기에 없는 집계가 있다는 사실을 숨기지 않는다.
    assert "전량 패키지" in readme


async def test_qc_run_table_keeps_a_row_per_run(client: AsyncClient, admin_token: str):
    """검사 실행 표가 같은 작업의 행을 실행마다 따로 저장하고 모델 칸의 빈 값을 NULL로 두는지 확인한다.

    행을 직접 넣어 표의 저장 형태만 확인한다. 검사 실행이 제출·승인 결과를 덮어쓰지 않고 쌓는지는
    실행 경로를 지나는 `caption_qc/test_caption_qc_runner.py`의 `test_both_triggers_accumulate`가 확인한다.
    """
    from api_gateway.models import CaptionQcRun
    from sqlalchemy import select

    pid = await new_project(client, admin_token, "qcruns")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid, start=10.0, end=40.0)

    async with SessionLocal() as s:
        s.add(CaptionQcRun(work_item_id=UUID(wid), trigger="submit", verdict="pass",
                           findings=[], adapter_key="rules"))
        s.add(CaptionQcRun(
            work_item_id=UUID(wid), trigger="approve", verdict="needs_review",
            findings=[{"check": "profanity", "severity": "warn", "message": "부적절 표현",
                       "sentence_id": "s1"}],
            adapter_key="dummy-llm", model="test-model", prompt_hash="abc123",
        ))
        await s.commit()
        runs = (await s.scalars(
            select(CaptionQcRun).where(CaptionQcRun.work_item_id == UUID(wid))
        )).all()

    # 순서로 찾지 않는다. 같은 트랜잭션의 `now()`는 값이 같아 created_at 정렬이 동률이다.
    by_trigger = {r.trigger: r for r in runs}
    assert set(by_trigger) == {"submit", "approve"}  # 덮어쓰지 않고 둘 다 남았다
    # 규칙만 돈 실행은 모델을 부르지 않는다. 값은 빈 문자열이 아니라 NULL이다.
    assert by_trigger["submit"].model is None and by_trigger["submit"].prompt_hash is None
    assert by_trigger["approve"].model == "test-model"
    assert by_trigger["approve"].prompt_hash == "abc123"
    assert by_trigger["approve"].findings[0]["sentence_id"] == "s1"
def test_sentence_count_falls_back_to_splitting_the_string():
    """문장 배열이 없는 옛 payload도 캡션이 있으면 문장 수가 0이 아닌지 확인한다.

    화면은 같은 문자열을 나눠 표시하므로 배지만 배열 길이로 세면 캡션이 있는 건이 `캡션 0`으로 표시된다.
    """
    from api_gateway.services.exports.export_service import _split_sentences

    assert _split_sentences("한 문장입니다.") == ["한 문장입니다."]
    assert len(_split_sentences("첫째입니다. 둘째입니다. 셋째입니다.")) == 3
    # 종결부호가 없어도 한 문장으로 센다. 0으로 세면 캡션이 없다는 틀린 정보가 된다.
    assert _split_sentences("종결부호가 없는 캡션") == ["종결부호가 없는 캡션"]
    assert _split_sentences("") == []
    assert _split_sentences("   ") == []


# 제외 사유의 이름


def test_every_schema_field_has_a_screen_name():
    """`ClipMetadata`의 모든 필드에 화면 이름이 있는지 확인한다. 없으면 키 원문이 화면에 나간다.

    사전(`FIELD_LABEL`)은 필드를 더할 때 함께 더해야 하는데, 기억에 맡기면 빠진다.
    세부 묘사는 항목마다 입력란이 따로 있어서 하위 키까지 확인한다.
    """
    from api_gateway.services.exports.export_package import FIELD_LABEL, field_label
    from reborn_contracts.clip_metadata import ClipDescription, ClipMetadata

    paths = list(ClipMetadata.model_fields) + [
        f"description.{k}" for k in ClipDescription.model_fields
    ]
    missing = [p for p in paths if p not in FIELD_LABEL]
    assert not missing, f"화면 이름이 없는 스키마 필드: {missing}"
    # 목록 원소와 깊은 하위 키는 부모 이름을 쓴다. 사전을 스키마만큼 키우지 않는다.
    assert field_label("speakers.0.start_time") == "화자 구간"
    assert field_label("review.reviewed_at") == "검수"


def test_unknown_validation_error_is_not_shown_in_english():
    """사전에 없는 pydantic 오류도 영문 원문 대신 `형식 오류`로 표시하는지 확인한다."""
    from api_gateway.services.exports.export_package import _msg_of

    got = _msg_of({"type": "some_new_kind", "loc": ("caption",), "msg": "Value error, nope"})
    assert got == "형식 오류"
    assert _msg_of({"type": "uuid_parsing", "loc": ("clip_id",), "msg": "x"}) == "형식 오류"


def test_empty_caption_is_reported_as_missing_not_too_short():
    """비어 있는 캡션을 `10자 이상 필요`가 아니라 `값 없음`으로 표시하는지 확인한다."""
    from api_gateway.services.exports.export_package import _msg_of

    short = {"type": "string_too_short", "loc": ("caption",), "input": "짧음",
             "ctx": {"min_length": 10}}
    assert _msg_of(short) == "10자 이상 필요"
    for empty in ("", "   ", None):
        err = {"type": "string_too_short", "loc": ("caption",), "input": empty,
               "ctx": {"min_length": 10}}
        assert _msg_of(err) == "값 없음", empty


def test_metadata_refuses_unknown_fields():
    """스키마에 없는 필드를 거절하는지 확인한다.

    조립 코드의 오타 때문에 값이 조용히 버려지지 않게 한다.
    거절된 행은 `정의되지 않은 항목` 사유로 제외 목록에 나온다.
    하위 객체는 문서와 같이 추가 필드를 허용한다.
    """
    from api_gateway.services.exports.export_package import _msg_of
    from reborn_contracts.clip_metadata import ClipMetadata

    base = dict(
        clip_id=uuid4(), source_video_id=uuid4(), start_time=0.0, end_time=4.0,
        duration=4.0, caption="열 자가 넘는 캡션입니다",
    )
    with pytest.raises(ValidationError) as ei:
        ClipMetadata(**base, on_screen_txt=["오타"])
    err = ei.value.errors()[0]
    assert err["type"] == "extra_forbidden"
    assert _msg_of(err) == "정의되지 않은 항목"
    # 조립 경로는 알려진 이름만 넘기므로 영향이 없다.
    assert build_metadata(_work(_PAYLOAD), _video(), _clip(), _NOW, 0.0).caption


def test_report_counts_exclusions_by_reason(tmp_path: Path):
    """리포트에 사유별 집계가 있고 첫 사유로 세서 합이 제외 수와 같은지 확인한다."""
    rows, approved = [], {}
    for payload, rights in (
        ({"caption": {"brief": "짧다"}}, "auto_cleared"),
        ({"caption": {"brief": "짧다"}}, "auto_cleared"),
        (_PAYLOAD, "pending"),
    ):
        w = _work(payload)
        approved[w.work_item_id] = _NOW
        rows.append((w, _video(), _clip(rights=rights)))
    report = write_package(rows, approved, tmp_path / "p.zip", project_name="t")
    assert report["excluded_by_reason"] == {"캡션": 2, "비식별화 신고": 1}
    assert sum(report["excluded_by_reason"].values()) == report["excluded"]
    assert report["exclusions_truncated"] is False


def test_report_says_when_the_exclusion_list_is_cut(tmp_path: Path, monkeypatch):
    """제외 목록이 상한에서 잘리면 잘렸다고 적는지 확인한다. 수는 전체 기준이고 목록만 짧다."""
    from api_gateway.services.exports import export_package

    monkeypatch.setattr(export_package, "EXCLUSIONS_LISTED", 2)
    rows, approved = [], {}
    for _ in range(3):
        w = _work({"caption": {"brief": "짧다"}})
        approved[w.work_item_id] = _NOW
        rows.append((w, _video(), _clip()))
    report = write_package(rows, approved, tmp_path / "p.zip", project_name="t")
    assert report["excluded"] == 3
    assert len(report["exclusions"]) == 2
    assert report["exclusions_truncated"] is True
    assert report["excluded_by_reason"] == {"캡션": 3}     # 집계는 잘리지 않는다


# 무결성


def test_package_refuses_an_index_that_does_not_match_its_files(tmp_path: Path):
    """색인 줄 수와 `metadata/` 파일 수가 다르면 패키지를 만들지 않는지 확인한다.

    같은 클립이 두 번 실리면 같은 이름으로 두 번 쓰인다(`zipfile`은 경고만 한다).
    그러면 색인은 두 줄인데 한 파일이 다른 파일을 덮고, 받는 쪽은 그 사실을 알 수 없다.
    """
    from api_gateway.services.exports.export_package import PackageIntegrityError

    clip = _clip()
    a, b = (_work(_PAYLOAD), _video(), clip), (_work(_PAYLOAD), _video(), clip)
    approved = {a[0].work_item_id: _NOW, b[0].work_item_id: _NOW}
    with pytest.warns(UserWarning), pytest.raises(PackageIntegrityError):
        write_package([a, b], approved, tmp_path / "p.zip", project_name="t")


def test_verify_catches_a_checksum_that_does_not_match(tmp_path: Path):
    """패키지를 닫은 뒤 다시 열어 체크섬까지 대조하는지 확인한다.

    색인이 가리키는 바이트가 색인의 체크섬과 같은지 확인한다.
    """
    from api_gateway.services.exports.export_package import PackageIntegrityError, _verify

    out = tmp_path / "p.zip"
    line = {"metadata_path": "metadata/x.json", "checksum": "xxh64:0000000000000000"}
    with ZipFile(out, "w") as z:
        z.writestr("metadata/x.json", b'{"a": 1}')
        z.writestr("manifest.jsonl", json.dumps(line) + "\n")
    with pytest.raises(PackageIntegrityError):
        _verify(out, [line])


# 데이터셋 카드


def test_dataset_card_survives_a_project_name_with_yaml_syntax(tmp_path: Path):
    """프로젝트 이름에 `:`·`#`가 있어도 카드가 YAML로 읽히는지 확인한다.

    f-문자열로 붙이면 `name: 샘플 방송: 9시 뉴스 샘플`가 중첩 맵이 되거나 `#` 뒤가 주석으로 사라진다.
    """
    name = "샘플 방송: 9시 뉴스 샘플 #2 - 특집"
    w = _work(_PAYLOAD)
    out = tmp_path / "p.zip"
    write_package([(w, _video(), _clip())], {w.work_item_id: _NOW}, out, project_name=name)
    with ZipFile(out) as z:
        card = yaml.safe_load(z.read("dataset_card.yaml").decode())
    assert card["name"] == name
    assert card["license"]["platform_source"] == "Apache-2.0"


# 이력 행과 다운로드


async def test_download_before_completion_is_409(client: AsyncClient, admin_token: str):
    """끝나지 않은 내보내기를 받을 수 없고 `export_status_pending`을 돌려주는지 확인한다."""
    pid = await new_project(client, admin_token, "exp409")
    eid = (await client.post(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()["export_id"]
    r = await client.get(
        f"/exports/{eid}/download", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "export_status_pending"


async def test_export_row_says_what_it_excluded_and_how_to_name_it(
    client: AsyncClient, admin_token: str
):
    """이력 행에 제외 수·시도 수·파일 이름을 싣는지 확인한다.

    화면이 문장을 해석해 값을 꺼내지 않게 한다.
    """
    pid = await new_project(client, admin_token, "exprow")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    await _approve(w1, _PAYLOAD)
    await _approve(w2, {"caption": {"brief": "짧다"}})
    eid = (await client.post(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()["export_id"]
    await _flush_pending()

    rows = (await client.get(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    row = next(r for r in rows if r["export_id"] == eid)
    assert row["status"] == "completed", row
    assert row["item_count"] == 1 and row["excluded_count"] == 1
    assert row["error"] is None          # 완료된 행에 문장을 싣지 않는다
    assert row["attempts"] == 1 and row["max_attempts"] >= 1
    assert re.fullmatch(
        r"exprow-[a-z0-9-]+-\d{8}-\d{4}-" + eid[:8] + r"\.zip", row["filename"]
    ), row

    d = (await client.get(
        f"/exports/{eid}/download", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    # 받는 파일 이름이 서명에 들어 있다. 모두 `package.zip`으로 받아지지 않게 한다.
    assert "response-content-disposition" in d["download_url"]
    assert row["filename"] in d["download_url"]


# 완료 목록 집계와 이력의 사람


async def test_completed_summary_counts_once_regardless_of_search(
    client: AsyncClient, admin_token: str
):
    """집계를 요약 요청 한 번으로 받고 목록은 `summary=false`면 페이지마다 다시 세지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "csum")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    await _approve(w1, _PAYLOAD)
    await _approve(w2, {"caption": {"brief": "짧다"}})

    s = (await client.get(
        f"/projects/{pid}/completed/summary", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    assert s["total"] == 2 and s["deliverable_total"] == 1
    assert s["deliverable_scan_limit"] > 0

    page = (await client.get(
        f"/projects/{pid}/completed",
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"summary": "false", "q": "없는말"},
    )).json()
    assert page["total"] == 0
    assert page["deliverable_total"] is None     # 세지 않았다. 화면은 요약에서 읽는다.


async def test_completed_summary_gives_deliverable_length_and_last_approval(
    client: AsyncClient, admin_token: str
):
    """요약이 납품 가능 건의 길이 합과 마지막 승인 시각을 주고 뷰어도 같은 값을 받는지 확인한다.

    길이는 건수와 같은 검사에서 센다. 납품 불가 건과 품질검수 보류분의 승인은 넣지 않는다.
    """
    from datetime import timedelta

    from api_gateway.models import Review
    from sqlalchemy import select, update

    pid = await new_project(client, admin_token, "csum-len")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=12.5)
    _c2, w2 = await seed_clip_work(pid, vid, start=20.0, end=30.0)
    _c3, w3 = await seed_clip_work(pid, vid, start=40.0, end=50.0)
    await _approve(w1, _PAYLOAD)
    await _approve(w2, {"caption": {"brief": "짧다"}})
    await _approve(w3, _PAYLOAD)
    later = datetime.now(UTC) + timedelta(days=1)
    async with SessionLocal() as s:
        # 보류분은 완료 목록에서 빠지므로 그 승인 시각은 마지막 승인 시각에 들어가지 않는다.
        await s.execute(update(WorkItem).where(WorkItem.work_item_id == UUID(w3))
                        .values(qc_status="held"))
        await s.execute(update(Review).where(Review.work_item_id == UUID(w3))
                        .values(reviewed_at=later))
        expected = await s.scalar(
            select(Review.reviewed_at).where(Review.work_item_id.in_([UUID(w1), UUID(w2)]))
            .order_by(Review.reviewed_at.desc()).limit(1)
        )
        await s.commit()

    _, reviewer = await add_member(client, admin_token, pid, "reviewer")
    for tok in (admin_token, reviewer):
        body = (await client.get(
            f"/projects/{pid}/completed/summary", headers={"Authorization": f"Bearer {tok}"}
        )).json()
        assert body["total"] == 2 and body["deliverable_total"] == 1
        assert body["deliverable_seconds"] == 12.5
        assert datetime.fromisoformat(body["last_approved_at"]) == expected


async def test_completed_summary_length_is_none_when_not_counted(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """상한을 넘어 세지 않았으면 길이 합도 0이 아니라 `None`인지 확인한다. 건수와 같은 규칙이다."""
    from api_gateway.services.exports import export_service

    pid = await new_project(client, admin_token, "csum-cap")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    await _approve(w1, _PAYLOAD)
    await _approve(w2, _PAYLOAD)
    monkeypatch.setattr(export_service, "DELIVERABLE_SCAN_LIMIT", 1)
    body = (await client.get(
        f"/projects/{pid}/completed/summary", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    assert body["deliverable_total"] is None
    assert body["deliverable_seconds"] is None
    assert body["last_approved_at"] is not None


async def test_reviewer_can_read_completed_summary(client: AsyncClient, admin_token: str):
    """요약도 목록과 같은 권한을 쓰는지 확인한다. 검수자는 볼 수 있고 비멤버는 볼 수 없다."""
    pid = await new_project(client, admin_token, "csum2")
    _, reviewer = await add_member(client, admin_token, pid, "reviewer")
    _, outsider = await make_user(client, admin_token, "csumout")
    ok = await client.get(
        f"/projects/{pid}/completed/summary", headers={"Authorization": f"Bearer {reviewer}"}
    )
    assert ok.status_code == 200 and ok.json()["total"] == 0
    no = await client.get(
        f"/projects/{pid}/completed/summary", headers={"Authorization": f"Bearer {outsider}"}
    )
    assert no.status_code == 403


async def test_completed_detail_names_who_submitted_and_who_judged(
    client: AsyncClient, admin_token: str
):
    """이력에 수행한 사람의 이름까지 싣는지 확인한다. UUID만 주면 화면이 사람 이름을 표시할 수 없다."""
    from api_gateway.models import Label, Review, User
    from sqlalchemy import select

    pid = await new_project(client, admin_token, "detnames")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    await _approve(wid, _PAYLOAD)
    async with SessionLocal() as s:
        # 제출자는 판정을 남긴 사람과 같은 사람으로 고른다.
        # `select(User…).limit(1)`로 고르면 `_approve`의 같은 형식 질의와 다른 사람을 고를 수 있다.
        # `users`가 한 행이면 항상 같지만, 앞선 테스트가 계정(`annotator`·`reviewer`)을 남기면 달라진다.
        # 그러면 단독 실행에서는 통과하고 앞에 어떤 테스트가 돌았는지에 따라 간헐적으로 실패한다.
        uid, name = (await s.execute(
            select(User.user_id, User.display_name)
            .join(Review, Review.reviewer_id == User.user_id)
            .where(Review.work_item_id == UUID(wid))
        )).one()
        s.add(Label(work_item_id=UUID(wid), submitted_by=uid, label_data=_PAYLOAD))
        await s.commit()

    d = (await client.get(
        f"/projects/{pid}/completed/{wid}", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    assert d["reviews"][0]["reviewer_name"] == name
    assert d["revisions"][0]["submitted_by_name"] == name


def test_card_says_how_long_and_where_it_came_from(tmp_path: Path):
    """카드에 분량과 출처를 적는지 확인한다.

    받는 쪽은 주로 분량과 출처를 보려고 카드를 연다.
    분량은 담은 클립의 길이 합이다. `total_clips`와 같은 모집단이어야 두 값이 어긋나지 않는다.
    방송사·프로그램명·방영일은 영상 단위로 모은다.
    """
    ok = (_work(_PAYLOAD), _video(), _clip())
    out = tmp_path / "p.zip"
    write_package([ok], {ok[0].work_item_id: _NOW}, out, project_name="t",
                  generated_at=_NOW)

    with ZipFile(out) as z:
        card = yaml.safe_load(z.read("dataset_card.yaml").decode())
        line = json.loads(z.read("manifest.jsonl").decode().strip())

    # 색인의 길이 합과 같은 수다.
    import math
    assert card["total_duration_hours"] == math.ceil(line["duration"] / 36) / 100
    # 클립이 있으면 분량은 0보다 크다. 짧은 클립 하나짜리 패키지가 분량 없음으로 읽히지 않게 한다.
    assert card["total_duration_hours"] > 0
    assert card["source"]["broadcaster"] == "샘플 방송"
    assert card["source"]["program_count"] == 1
    assert card["source"]["aired_range"] == [_NOW.date().isoformat()] * 2


def test_aired_date_follows_operating_timezone(tmp_path: Path):
    """방영일은 한국 시각 자정으로 저장되므로 카드와 클립 메타가 한국 날짜로 싣는지 확인한다.

    UTC로 날짜를 자르면 2026-07-01 방영분이 2026-06-30으로 하루 앞당겨진다.
    클립 메타는 같은 시각을 +09:00 오프셋의 ISO 8601로 싣는다. 형식은 스키마의 date-time 그대로다.
    """
    from datetime import timedelta, timezone

    kst_midnight = datetime(2026, 7, 1, tzinfo=timezone(timedelta(hours=9))).astimezone(UTC)
    ok = (_work(_PAYLOAD), _video(aired_at=kst_midnight), _clip())
    out = tmp_path / "p.zip"
    write_package([ok], {ok[0].work_item_id: _NOW}, out, project_name="t",
                  generated_at=_NOW)

    with ZipFile(out) as z:
        card = yaml.safe_load(z.read("dataset_card.yaml").decode())
        meta = json.loads(z.read(f"metadata/{ok[2].clip_id}.json").decode())

    assert card["source"]["aired_range"] == ["2026-07-01", "2026-07-01"]
    assert meta["aired_at"] == "2026-07-01T00:00:00+09:00"


def test_card_does_not_invent_a_source_it_does_not_know(tmp_path: Path):
    """메타가 빈 영상이면 카드에 출처 항목을 아예 내지 않는지 확인한다.

    `broadcaster: ""`를 내면 받는 쪽이 방송사가 없는 자료라는 사실로 읽는다.
    비어 있는 값과 알 수 없는 값은 다르다.
    """
    bare = _video(broadcaster=None, program_name=None, aired_at=None)
    ok = (_work(_PAYLOAD), bare, _clip())
    out = tmp_path / "p.zip"
    write_package([ok], {ok[0].work_item_id: _NOW}, out, project_name="t",
                  generated_at=_NOW)

    with ZipFile(out) as z:
        card = yaml.safe_load(z.read("dataset_card.yaml").decode())

    assert card["source"] == {}, card["source"]
    # 분량은 메타와 관계없이 나온다. 길이는 클립에 있다.
    assert card["total_duration_hours"] > 0
