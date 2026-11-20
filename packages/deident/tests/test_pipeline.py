"""합성 영상으로 비식별화 전 과정을 돌려 결과 영상, 오디오, 좌표, 보고를 확인한다."""

import json
import subprocess

import numpy as np
import pytest
from essential_deident import anonymize, video


@pytest.fixture
def clip(tmp_path):
    """오디오가 있는 2초짜리 320×240 30fps 합성 영상을 만든다."""
    path = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=30:duration=2", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


class FixedBox:
    """모든 프레임에서 같은 상자 하나를 돌려주는 검출기."""

    device = "cpu"

    def detect_batch(self, frames):
        """프레임마다 고정 상자를 돌려준다."""
        return [[(100.0, 80.0, 160.0, 140.0, 0.9)] for _ in frames]


def test_anonymize_masks_and_keeps_frames_audio(clip, tmp_path):
    """결과 영상의 프레임 수와 오디오가 유지되고 상자 영역이 바뀐다."""
    dst, coords = tmp_path / "out.mp4", tmp_path / "c.json"
    report = anonymize(str(clip), str(dst), str(coords), detector=FixedBox())
    assert report["frames"] == 60 and report["masked"] is True
    assert report["review_needed"] is False and report["detection_rate"] == 1.0
    assert video.count_frames(str(dst)) == 60
    assert video.probe(str(dst)).has_audio
    doc = json.loads(coords.read_text())
    assert len(doc["frames"]) == 60 and report["coordinates"]["schema_version"] == 1
    src_frame = next(video.frames(str(clip))).astype(int)
    out_frame = next(video.frames(str(dst))).astype(int)
    inside = np.abs(src_frame[90:130, 110:150] - out_frame[90:130, 110:150]).mean()
    outside = np.abs(src_frame[:60, :] - out_frame[:60, :]).mean()
    assert inside > 3 * outside


def test_no_faces_requests_review(clip, tmp_path):
    """얼굴이 없으면 가림 없이 끝내고 검수 필요로 보고한다."""

    class Empty(FixedBox):
        def detect_batch(self, frames):
            """빈 결과를 돌려준다."""
            return [[] for _ in frames]

    report = anonymize(str(clip), str(tmp_path / "out.mp4"), detector=Empty())
    assert report["masked"] is False and report["review_needed"] is True
    assert report["review"] == ["no-detections"] and "coordinates" not in report


def test_unreadable_input_raises(tmp_path):
    """영상이 아닌 파일은 DeidentError로 실패한다."""
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(video.DeidentError):
        anonymize(str(bad), str(tmp_path / "out.mp4"), detector=FixedBox())
