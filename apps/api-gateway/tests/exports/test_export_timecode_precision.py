"""납품 시각의 밀리초 정밀도와 길이 일치 테스트.

납품 시각은 소수 3자리로 쓰고, 납품 규격은 `duration == end_time - start_time`(오차 1ms 이내)을 요구한다.
float 덧셈·뺄셈은 `32.89999999999998` 같은 값을 남기므로 메타 파일과 `manifest.jsonl`에 실린 글자 그대로를 확인한다.
"""
import json
import random
from pathlib import Path
from zipfile import ZipFile

import pytest
from api_gateway.services.exports.export_package import build_metadata, write_package
from tests.exports.test_exports import _NOW, _PAYLOAD, _clip, _video, _work

_TIME_KEYS = ("start_time", "end_time", "duration")


def _decimals(text: str) -> int:
    """JSON 숫자 글자의 소수 자릿수를 센다."""
    return len(text.split(".", 1)[1]) if "." in text else 0


def _package_lines(tmp_path: Path, start: float, end: float, offset: float = 0.0):
    """클립 하나로 패키지를 만들고 (메타 파일, 색인 한 줄)을 숫자 글자 그대로 읽어 돌려준다.

    `parse_float=str`로 읽어야 직렬화된 글자를 볼 수 있다. float로 읽으면 반올림 여부가 가려진다.
    """
    w = _work(_PAYLOAD)
    v = _video()
    if offset:
        v.analysis_uri = "s3://bkt/master/a.mp4"
        v.extra = {**v.extra, "probe": {**v.extra["probe"], "start_time_seconds": offset}}
    out = tmp_path / "p.zip"
    report = write_package([(w, v, _clip(start, end))], {w.work_item_id: _NOW}, out,
                           project_name="t")
    assert report["included"] == 1, report
    with ZipFile(out) as z:
        line = json.loads(z.read("manifest.jsonl").decode().splitlines()[0], parse_float=str)
        meta = json.loads(z.read(line["metadata_path"]), parse_float=str)
    return meta, line


def test_reported_clip_ships_duration_in_milliseconds(tmp_path: Path):
    """774.767~807.667 구간의 길이를 `32.9`로 싣고 메타와 색인이 같은 값을 싣는지 확인한다."""
    meta, line = _package_lines(tmp_path, 774.767, 807.667)

    for doc in (meta, line):
        assert doc["start_time"] == "774.767"
        assert doc["end_time"] == "807.667"
        assert doc["duration"] == "32.9"


@pytest.mark.parametrize("offset", [1.467, 0.1, 0.033])
def test_offset_shift_keeps_milliseconds(tmp_path: Path, offset: float):
    """원본 축 보정값을 더한 시각도 소수 3자리 이내로 싣고 메타와 색인이 같은지 확인한다."""
    meta, line = _package_lines(tmp_path, 774.767, 807.667, offset)

    for key in _TIME_KEYS:
        assert _decimals(meta[key]) <= 3, (key, meta[key])
        assert meta[key] == line[key], key
    assert float(meta["start_time"]) == pytest.approx(774.767 + offset, abs=5e-4)
    assert meta["source_start_offset"] == str(offset)


def test_duration_matches_shipped_times_within_one_ms():
    """반올림한 시각의 차가 길이와 1ms 안에서 맞고 모든 시각이 소수 3자리 이내인지 표본 500개로 확인한다."""
    rng = random.Random(20261001)
    for _ in range(500):
        start = round(rng.uniform(0, 20000), 3)
        end = round(start + rng.uniform(2.0, 600), 3)
        offset = rng.choice([0.0, 0.033, 0.1, 1.467, 1.466667])
        m = build_metadata(_work(_PAYLOAD), _video(), _clip(start, end), _NOW, offset)

        for key in _TIME_KEYS:
            value = getattr(m, key)
            assert _decimals(repr(value)) <= 3, (key, value, start, end, offset)
        assert abs(m.duration - (m.end_time - m.start_time)) <= 0.001, (start, end, offset)
