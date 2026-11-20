"""잡 러너가 산출물을 올리기 전에 한 번 읽어 측정값만 싣는지 확인한다.

판정은 받는 쪽이 한다. 러너는 읽지 못한 산출도 예외 없이 그 사실을 싣는다.
마지막 두 테스트는 실제 FFmpeg로 `output_check` 모듈 docstring의 사례를 재현한다.
잘린 입력도 rc=0으로 끝나 짧은 사본이 나온다(ffmpeg가 없으면 skip).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import reborn_transcode.job_runner as jr
from reborn_transcode.output_check import DURATION_MISMATCH, check_output
from reborn_transcode.probe import ProbeError, ProbeTimeoutError, probe
from reborn_transcode.transcode import TargetSpec, TranscodeError, transcode_streaming


def _job(*labels: str) -> dict:
    """타깃 라벨만 다른 최소 잡 페이로드를 만든다."""
    return {
        "video_id": "v", "token": "t", "input_url": "http://x/in.mp4", "heartbeat_every_s": 60,
        "targets": [{"label": lb, "height": None, "crf": 24, "put_url": f"http://x/{lb}",
                     "content_type": "video/mp4"} for lb in labels],
    }


def _encode(input_uri, output_path, spec, *, on_progress=None, cancel=None, ffmpeg="ffmpeg"):
    """인코딩을 흉내 내 파일만 남긴다. 러너의 흐름 제어는 실제 코드가 돈다."""
    output_path.write_bytes(b"\x00" * 8)


#: probe()의 반환 형식 그대로의 가짜 측정값.
#: 러너가 그중 어떤 필드만 싣는지 보려고 필드를 모두 채운다.
_META = {"duration_seconds": 12.5, "size_bytes": 8, "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
         "major_brand": "isom", "video_codec": "h264", "width": 1280, "height": 720,
         "fps": 29.97, "start_time_seconds": 0.0}


def test_each_output_is_read_before_it_is_uploaded(monkeypatch):
    """산출물마다 올리기 전에 먼저 읽고, 받는 쪽이 쓰는 필드만 싣는다."""
    seen: list[tuple[str, str]] = []

    def _probe(uri, *, timeout=60, ffprobe="ffprobe"):
        """읽은 파일 이름을 기록하고 고정 측정값을 돌려준다."""
        seen.append(("probe", Path(uri).name))
        return dict(_META)

    monkeypatch.setattr(jr, "transcode_streaming", _encode)
    monkeypatch.setattr(jr, "probe", _probe)
    monkeypatch.setattr(jr, "_put", lambda url, path, ct: seen.append(("put", url)))

    r = jr.run_job(_job("analysis-720p"))

    # 받는 쪽이 저장·비교하는 필드만 싣는다. 컨테이너 이름·시작 시각은 원본에 관한 값이다.
    assert r["outputs"] == [{"label": "analysis-720p", "duration_seconds": 12.5, "fps": 29.97,
                             "video_codec": "h264", "width": 1280, "height": 720,
                             "size_bytes": 8}]
    put_at = seen.index(("put", "http://x/analysis-720p"))
    assert seen.index(("probe", "analysis-720p.mp4")) < put_at, "올리기 전에 읽는다"


def test_outputs_follow_the_target_order(monkeypatch):
    """`outputs`는 타깃 순서를 따른다."""
    monkeypatch.setattr(jr, "transcode_streaming", _encode)
    monkeypatch.setattr(jr, "probe", lambda uri, **kw: dict(_META))
    monkeypatch.setattr(jr, "_put", lambda *a: None)
    r = jr.run_job(_job("analysis-720p", "play-480p"))
    assert [o["label"] for o in r["outputs"]] == ["analysis-720p", "play-480p"]


def test_an_unreadable_output_is_reported_not_raised(monkeypatch):
    """읽지 못한 산출도 그 사실을 싣는다.

    `JobError`로 바꾸면 원본 길이가 없는 워커가 판정하게 된다.
    """
    said = "ffprobe failed (rc=1): Invalid data found when processing input"

    def _probe(uri, **kw):
        """산출물을 읽지 못한 경우를 흉내 낸다."""
        raise ProbeError(said)

    uploaded: list[str] = []
    monkeypatch.setattr(jr, "transcode_streaming", _encode)
    monkeypatch.setattr(jr, "probe", _probe)
    monkeypatch.setattr(jr, "_put", lambda url, path, ct: uploaded.append(url))

    r = jr.run_job(_job("play-480p"))
    assert r["outputs"] == [{"label": "play-480p", "error": said}]
    assert uploaded == ["http://x/play-480p"], "버릴지는 판정하는 쪽이 정한다"


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg 없음")


def _make_source(path: Path, seconds: int) -> None:
    """영상·음성이 모두 있는 짧은 원본을 만든다. faststart라 앞부분(moov)만 남겨도 열린다."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-movflags", "+faststart", str(path)],
        check=True, capture_output=True,
    )


@needs_ffmpeg
def test_a_real_output_is_read_with_its_facts(tmp_path: Path):
    """실제 파일을 읽으면 코덱·해상도·fps·길이·크기가 실린다."""
    src = tmp_path / "src.mp4"
    _make_source(src, 4)
    facts = jr._inspect("analysis-720p", src)
    assert facts["video_codec"] == "h264"
    assert (facts["width"], facts["height"]) == (320, 240)
    assert facts["fps"] == 25.0
    assert facts["duration_seconds"] == pytest.approx(4.0, abs=0.1)
    assert facts["size_bytes"] == src.stat().st_size


@needs_ffmpeg
def test_a_real_truncated_input_is_caught(tmp_path: Path):
    """잘린 입력에서 나온 사본은 종료 코드가 성공이어도 받는 쪽 규칙이 잡아낸다."""
    src = tmp_path / "src.mp4"
    _make_source(src, 8)
    cut = tmp_path / "cut.mp4"
    data = src.read_bytes()
    cut.write_bytes(data[: len(data) // 2])

    out = tmp_path / "out.mp4"
    try:
        # ffmpeg 8.1은 디코딩 경고만 찍고 rc=0으로 끝난다. 예외가 나지 않는다.
        transcode_streaming(str(cut), out, TargetSpec("analysis-720p", None, 24,
                                                      preset="ultrafast"))
    except TranscodeError:
        pytest.skip("이 ffmpeg는 잘린 입력을 오류로 끝낸다 — 검사할 사본이 없다")

    check = check_output(probe(str(src)), jr._inspect("analysis-720p", out))
    assert check["cause"] == DURATION_MISMATCH
    assert check["delta_seconds"] is not None and check["delta_seconds"] < -2.0


def _timeouts(times: int, calls: list):
    """앞의 `times`번은 시간을 넘기고 그다음은 측정값을 돌려주는 가짜 probe를 만든다."""
    def _probe(uri, *, timeout=60, ffprobe="ffprobe"):
        """받은 제한 시간을 기록한다."""
        calls.append(timeout)
        if len(calls) <= times:
            raise ProbeTimeoutError(f"ffprobe timeout after {timeout}s")
        return dict(_META)
    return _probe


def test_a_slow_check_is_read_again_with_more_time(monkeypatch):
    """검사가 한 번 시간을 넘기면 제한 시간을 늘려 다시 읽는다. 끝나면 측정값을 싣는다."""
    calls: list = []
    monkeypatch.setattr(jr, "transcode_streaming", _encode)
    monkeypatch.setattr(jr, "probe", _timeouts(1, calls))
    monkeypatch.setattr(jr, "_put", lambda url, path, ct: None)

    r = jr.run_job(_job("play-480p"))

    assert calls == [jr._INSPECT_TIMEOUT_S, jr._INSPECT_TIMEOUT_S * 2]
    assert r["outputs"][0]["duration_seconds"] == 12.5


def test_a_check_that_keeps_timing_out_is_marked(monkeypatch):
    """다시 읽어도 시간을 넘기면 원문과 함께 시간 초과 표시를 싣는다. 판정은 받는 쪽이 한다."""
    calls: list = []
    monkeypatch.setattr(jr, "transcode_streaming", _encode)
    monkeypatch.setattr(jr, "probe", _timeouts(9, calls))
    monkeypatch.setattr(jr, "_put", lambda url, path, ct: None)

    r = jr.run_job(_job("play-480p"))

    assert len(calls) == jr._INSPECT_RETRIES + 1
    assert r["outputs"] == [{"label": "play-480p", "timed_out": True,
                             "error": f"ffprobe timeout after {calls[-1]}s"}]
