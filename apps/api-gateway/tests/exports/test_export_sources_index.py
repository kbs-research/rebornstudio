"""납품 패키지의 원본 영상 목록(`sources.csv`) 테스트.

메타 JSON만 받은 쪽이 `source_video_id`로 원본 파일을 찾을 수 있어야 한다.
열 이름과 순서, 영상당 한 행, 클립 수, 수식 시작 글자 막기, 엑셀용 BOM을 확인한다.
"""
import csv
import io
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from api_gateway.services.exports import export_package
from api_gateway.services.exports.export_package import (
    OPTIONAL_LABEL_FIELDS,
    SOURCES_COLUMNS,
    PackageIntegrityError,
    write_package,
)
from tests.exports.test_exports import _NOW, _PAYLOAD, _clip, _video, _work

_HEADER = (
    "source_video_id,title,original_filename,storage_key,source_checksum,source_duration,"
    "broadcaster,program_name,episode,genre,aired_at,clip_count"
)


def _read(out: Path) -> tuple[bytes, list[dict[str, str]], list[dict], dict]:
    """패키지에서 `sources.csv` 원문, 그 행, 메타 파일, 리포트를 읽는다."""
    with ZipFile(out) as z:
        raw = z.read("sources.csv")
        metas = [json.loads(z.read(n)) for n in z.namelist() if n.startswith("metadata/")]
        report = json.loads(z.read("report.json"))
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    return raw, rows, metas, report


def _pack(tmp_path: Path, rows, **kw) -> Path:
    """행 목록으로 패키지를 만들고 경로를 돌려준다. 모든 행을 같은 시각에 승인한 것으로 둔다."""
    out = tmp_path / f"{uuid4().hex}.zip"
    write_package(rows, {r[0].work_item_id: _NOW for r in rows}, out, project_name="t", **kw)
    return out


def test_header_is_fixed_and_starts_with_a_bom(tmp_path: Path):
    """열 이름과 순서가 고정값이고 파일이 UTF-8 BOM으로 시작하는지 확인한다."""
    out = _pack(tmp_path, [(_work(_PAYLOAD), _video(), _clip())])
    raw, _rows, _metas, _report = _read(out)
    assert raw.startswith(b"\xef\xbb\xbf"), "BOM이 없으면 엑셀이 한글 제목을 깨뜨린다"
    assert raw.decode("utf-8-sig").splitlines()[0] == _HEADER
    assert ",".join(SOURCES_COLUMNS) == _HEADER


def test_one_row_per_source_video_with_clip_counts(tmp_path: Path):
    """원본 영상마다 한 행이고 `clip_count`가 그 영상의 담긴 클립 수인지 확인한다."""
    a = _video(title="A", extra={"key": "news/2026/A.mp4"}, source_sha256="sha256:aa")
    b = _video(title="B", extra={"original_filename": "B 원본.mov"})
    rows = [
        (_work(_PAYLOAD), a, _clip(0.0, 10.0)),
        (_work(_PAYLOAD), a, _clip(10.0, 20.0)),
        (_work(_PAYLOAD), b, _clip(0.0, 5.0)),
    ]
    _raw, got, metas, _report = _read(_pack(tmp_path, rows))
    by_id = {r["source_video_id"]: r for r in got}
    assert len(got) == 2 and set(by_id) == {str(a.video_id), str(b.video_id)}
    assert by_id[str(a.video_id)]["clip_count"] == "2"
    assert by_id[str(b.video_id)]["clip_count"] == "1"
    # 모든 메타 파일의 `source_video_id`가 목록에 있다. 이 연결이 원본을 찾는 유일한 경로다.
    assert {m["source_video_id"] for m in metas} == set(by_id)
    # 제목순으로 싣는다.
    assert [r["title"] for r in got] == ["A", "B"]


def test_row_values_match_the_clip_metadata(tmp_path: Path):
    """행의 체크섬·길이·방송 메타·방영일이 같은 영상 클립 메타의 값과 형식이 같은지 확인한다."""
    v = _video(title="9시 뉴스", episode="12회", genre="보도",
               extra={"key": "news/2026/0901.mp4"}, source_sha256="sha256:ab12")
    _raw, got, metas, _report = _read(_pack(tmp_path, [(_work(_PAYLOAD), v, _clip())]))
    (row,), (meta,) = got, metas
    for col in ("source_checksum", "broadcaster", "program_name", "episode", "genre", "aired_at"):
        assert row[col] == meta[col], col
    assert float(row["source_duration"]) == meta["source_duration"]
    assert row["title"] == "9시 뉴스"
    assert row["storage_key"] == "news/2026/0901.mp4"
    assert row["original_filename"] == "0901.mp4"


def test_original_filename_prefers_the_stored_name(tmp_path: Path):
    """저장한 원래 파일 이름을 키의 마지막 부분보다 먼저 쓰는지 확인한다."""
    v = _video(extra={"key": "a/b/renamed.mp4", "original_filename": "원래 이름.mp4"})
    _raw, (row,), _metas, _report = _read(_pack(tmp_path, [(_work(_PAYLOAD), v, _clip())]))
    assert row["original_filename"] == "원래 이름.mp4"
    assert row["storage_key"] == "a/b/renamed.mp4"


def test_uploaded_video_has_no_internal_key(tmp_path: Path):
    """업로드 영상은 내부 경로(`intake/…`)를 싣지 않고 빈 값으로 두는지 확인한다."""
    v = _video(source_uri=f"s3://bkt/intake/{uuid4()}/source", extra={"original_filename": "a.mp4"})
    raw, (row,), _metas, _report = _read(_pack(tmp_path, [(_work(_PAYLOAD), v, _clip())]))
    assert row["storage_key"] == ""
    assert b"intake/" not in raw
    # 값이 없는 열은 빈 칸이다. `None`·`null` 문자열을 쓰지 않는다.
    assert row["source_checksum"] == "" and row["episode"] == ""


def test_excluded_clips_do_not_add_rows(tmp_path: Path):
    """담기지 않은 클립의 원본 영상은 목록에 넣지 않는지 확인한다. 카드의 출처와 같은 모집단이다."""
    kept = _video(title="담김")
    dropped = _video(title="빠짐")
    rows = [
        (_work(_PAYLOAD), kept, _clip()),
        (_work({"caption": {"brief": "짧다"}}), dropped, _clip()),
    ]
    _raw, got, _metas, report = _read(_pack(tmp_path, rows))
    assert [r["title"] for r in got] == ["담김"]
    assert report["excluded"] == 1


def test_empty_package_still_has_the_header(tmp_path: Path):
    """담긴 클립이 없어도 머리 행만 있는 파일을 싣는지 확인한다. 파일이 없으면 받는 쪽 스크립트가 멈춘다."""
    out = _pack(tmp_path, [])
    raw, got, _metas, _report = _read(out)
    assert got == []
    assert raw.decode("utf-8-sig").strip() == _HEADER


@pytest.mark.parametrize("lead", ["=", "+", "-", "@"])
def test_formula_leads_are_neutralized(tmp_path: Path, lead: str):
    """제목과 파일 이름이 수식 시작 글자로 시작하면 작은따옴표를 붙이는지 확인한다."""
    v = _video(title=f'{lead}HYPERLINK("http://x")',
               extra={"original_filename": f"{lead}1+2.mp4"})
    _raw, (row,), _metas, _report = _read(_pack(tmp_path, [(_work(_PAYLOAD), v, _clip())]))
    assert row["title"] == f"'{lead}HYPERLINK(\"http://x\")"
    assert row["original_filename"] == f"'{lead}1+2.mp4"
    # 수는 문자열로 바꾸지 않는다. 받는 쪽이 길이를 다시 합산할 수 있어야 한다.
    assert row["clip_count"] == "1"


def test_omit_optional_keeps_every_column(tmp_path: Path):
    """선택 항목 제외를 켜도 목록의 열과 값이 같은지 확인한다. 목록 열과 선택 항목 키는 서로 다른 집합이다."""
    v = _video(extra={"key": "x/a.mp4"}, source_sha256="sha256:cc")
    rows = [(_work(_PAYLOAD), v, _clip())]
    _r1, off, _m1, _p1 = _read(_pack(tmp_path, rows))
    _r2, on, _m2, report = _read(_pack(tmp_path, rows, omit_optional=True))
    assert report["omit_optional"] is True
    assert on == off
    assert not OPTIONAL_LABEL_FIELDS & set(SOURCES_COLUMNS)


def test_readme_explains_how_to_find_the_original(tmp_path: Path):
    """README가 `sources.csv`로 원본을 찾는 법과 파일 이름이 겹칠 수 있다는 점을 알리는지 확인한다."""
    out = _pack(tmp_path, [(_work(_PAYLOAD), _video(), _clip())])
    with ZipFile(out) as z:
        readme = z.read("README.md").decode()
    assert "`sources.csv`" in readme
    assert "source_video_id" in readme and "source_checksum" in readme
    assert "겹칠 수 있습니다" in readme and "중복" in readme


def test_mismatched_sources_stop_the_package(monkeypatch, tmp_path: Path):
    """색인과 원본 목록이 어긋나면 패키지를 만들지 않는지 확인한다."""
    real = export_package.source_row

    def broken(v, doc):
        """클립 수를 하나 더 센 행을 만든다."""
        row = real(v, doc)
        row["clip_count"] = 1
        return row

    monkeypatch.setattr(export_package, "source_row", broken)
    with pytest.raises(PackageIntegrityError):
        _pack(tmp_path, [(_work(_PAYLOAD), _video(), _clip())])
