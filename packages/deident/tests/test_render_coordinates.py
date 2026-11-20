"""모자이크 범위와 좌표 파일 형식을 확인한다."""

import json

import numpy as np
from essential_deident import coordinates, render


def test_mosaic_changes_only_padded_box():
    """여백을 더한 상자 안만 바뀌고 밖은 그대로다."""
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (100, 100, 3), dtype=np.uint8)
    before = frame.copy()
    render.mosaic(frame, [(40, 40, 60, 60)], pad=0.15)
    x1, y1, x2, y2 = render.pad_box((40, 40, 60, 60), 0.15, 100, 100)
    assert (x1, y1, x2, y2) == (37, 37, 63, 63)
    outside = np.ones((100, 100), bool)
    outside[y1:y2, x1:x2] = False
    assert (frame[outside] == before[outside]).all()
    assert not (frame[y1:y2, x1:x2] == before[y1:y2, x1:x2]).all()


def test_coordinates_file_matches_schema_v1(tmp_path):
    """좌표 파일에 형식 버전 1의 필드가 있고 상자가 있는 프레임만 실리는지 확인한다."""
    path = tmp_path / "c.json"
    coordinates.write(str(path), {0: [(10, 10, 20, 20)], 2: [(0, 0, 5, 5)]},
                      fps=30.0, frame_count=3, width=100, height=50, pad=0.1)
    doc = json.loads(path.read_text())
    assert doc["schema_version"] == 1 and doc["coordinate_space"] == "output_pixels"
    assert doc["boxes_include_padding"] is True and doc["sparse_frames"] is True
    assert [f["frame"] for f in doc["frames"]] == [0, 2]
    assert doc["frames"][0] == {"frame": 0, "time_ms": 0, "boxes": [[9, 9, 21, 21]]}
    assert doc["frames"][1]["time_ms"] == 67
    assert not (tmp_path / "c.json.tmp").exists()
