"""probe 단위 테스트. subprocess를 흉내 내므로 ffprobe가 없는 환경에서도 통과한다.

test_transcode.py와 같은 방침이다(CI에 ffmpeg가 없다).
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest
from reborn_transcode import ProbeError, ProbeTimeoutError, probe
from reborn_transcode.probe import _fps, build_command

_OK_JSON = {
    "format": {
        "duration": "120.000000",
        "size": "4477250",
        "bit_rate": "298483",
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        # ISO BMFF 계열은 디먹서 하나가 MOV·MP4·M4A를 모두 처리해 format_name이 한꺼번에
        # 나온다. 계열 안에서 실제 포맷은 이 brand로만 알 수 있다.
        "tags": {"major_brand": "isom", "minor_version": "512"},
    },
    "streams": [
        {"codec_type": "audio", "codec_name": "aac"},
        {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720,
         "r_frame_rate": "30/1", "start_time": "0.000000"},
    ],
}


def _proc(stdout: object = None, *, rc: int = 0, stderr: bytes = b""):
    """ffprobe 실행 결과를 흉내 낸다. stdout은 JSON으로 직렬화한다."""
    raw = json.dumps(stdout).encode() if stdout is not None else b""
    return SimpleNamespace(returncode=rc, stdout=raw, stderr=stderr)


def test_build_command_uses_json_and_no_download_flags():
    """URL을 그대로 넘긴다. 별도 다운로드 없이 ffprobe가 Range 요청으로 읽는다."""
    cmd = build_command("https://x/y.mp4?sig=1")
    assert cmd[0] == "ffprobe"
    assert cmd[-1] == "https://x/y.mp4?sig=1"
    assert "-print_format" in cmd and "json" in cmd
    assert "-show_format" in cmd and "-show_streams" in cmd


def test_probe_flattens_format_and_video_stream(monkeypatch):
    """format과 첫 video 스트림을 평평하게 합친다. 오디오 스트림은 건너뛴다."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(_OK_JSON))
    r = probe("s3-presigned-url")
    assert r == {
        "duration_seconds": 120.0,
        "size_bytes": 4477250,
        "bit_rate": 298483,
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "major_brand": "isom",
        "video_codec": "h264",
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "start_time_seconds": 0.0,
    }


def test_probe_missing_fields_become_none(monkeypatch):
    """컨테이너에 없는 필드는 None이다. 그 값의 판정은 규격 검증이 한다."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _proc({"format": {}, "streams": [{"codec_type": "video"}]}),
    )
    r = probe("x")
    assert r["duration_seconds"] is None
    assert r["size_bytes"] is None
    assert r["bit_rate"] is None
    assert r["video_codec"] is None
    assert r["fps"] is None
    assert r["start_time_seconds"] is None


def test_probe_reads_nonzero_start_time(monkeypatch):
    """시작 오프셋이 있는 원본의 시작 시각을 기록한다. 트랜스코딩이 이 값을 0으로 정규화한다.

    원본 video `start_time=1.500000`을 이 저장소의 인코딩 명령으로 변환하면
    마스터는 `0.033008`이 된다. 분석이 마스터를 읽으면 클립 타임코드가 그만큼 밀린다.
    원본은 지워질 수 있으므로 등록할 때 이 값을 잰다.
    """
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _proc({
            "format": {},
            "streams": [{"codec_type": "video", "start_time": "1.500000"}],
        }),
    )
    assert probe("x")["start_time_seconds"] == 1.5


def test_probe_bit_rate_falls_back_to_video_stream(monkeypatch):
    """컨테이너에 비트레이트가 없으면 비디오 스트림의 비트레이트를 쓴다."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _proc({
            "format": {"bit_rate": "N/A"},
            "streams": [{"codec_type": "video", "bit_rate": "8200000"}],
        }),
    )
    assert probe("x")["bit_rate"] == 8200000


def test_probe_raises_on_nonzero_rc(monkeypatch):
    """ffprobe가 0이 아닌 코드로 끝나면 ProbeError를 낸다."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(rc=1, stderr=b"boom"))
    with pytest.raises(ProbeError, match="rc=1"):
        probe("x")


def test_probe_error_redacts_presigned_signature(monkeypatch):
    """ffprobe가 출력한 입력 URL의 쿼리스트링은 지운다.

    호출부가 이 메시지를 DB(`videos.metadata.probe.error`)에 저장해 화면까지 보낸다.
    서명이 남으면 만료 전까지 유효한 객체 접근 권한이 그대로 유출된다.
    """
    stderr = (
        b"http://minio:9000/dev/a/source?X-Amz-Credential=minioadmin"
        b"&X-Amz-Signature=deadbeefcafe: Connection refused\n"
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(rc=1, stderr=stderr))
    with pytest.raises(ProbeError) as e:
        probe("x")

    msg = str(e.value)
    assert "X-Amz-Signature" not in msg
    assert "deadbeefcafe" not in msg
    assert "Connection refused" in msg  # 잘라내기 전에 지워야 사유가 남는다
    assert "http://minio:9000/dev/a/source?…" in msg  # 어느 객체였는지는 남긴다


def test_probe_raises_on_timeout(monkeypatch):
    """네트워크 대상이라 타임아웃이 필수다. 워커가 응답 없는 URL에 묶이지 않게 한다.

    시간 초과는 별도 예외 `ProbeTimeoutError`다. `ProbeError`만 잡는 호출부도 그대로 받는다.
    """
    def _raise(*a, **k):
        """ffprobe 타임아웃을 흉내 낸다."""
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=60)
    monkeypatch.setattr(subprocess, "run", _raise)
    with pytest.raises(ProbeTimeoutError, match="timeout"):
        probe("x", timeout=60)
    assert issubclass(ProbeTimeoutError, ProbeError)


def test_probe_raises_when_ffprobe_missing(monkeypatch):
    """api 이미지처럼 ffprobe가 없는 환경에서는 원인을 알 수 있는 메시지를 낸다."""
    def _raise(*a, **k):
        """ffprobe가 설치되지 않은 환경을 흉내 낸다."""
        raise FileNotFoundError
    monkeypatch.setattr(subprocess, "run", _raise)
    with pytest.raises(ProbeError, match="ffprobe not found"):
        probe("x")


def test_probe_raises_on_invalid_json(monkeypatch):
    """ffprobe 출력이 JSON이 아니면 ProbeError를 낸다."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"not json", stderr=b""),
    )
    with pytest.raises(ProbeError, match="not valid JSON"):
        probe("x")


def test_probe_raises_when_no_video_stream(monkeypatch):
    """오디오만 있는 파일은 MI에 보낼 대상이 아니므로 실패로 본다."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _proc({"format": {}, "streams": [{"codec_type": "audio"}]}),
    )
    with pytest.raises(ProbeError, match="no video stream"):
        probe("x")


@pytest.mark.parametrize(
    ("rate", "expected"),
    [("30/1", 30.0), ("30000/1001", 29.97), ("0/0", None), (None, None), ("bad", None)],
)
def test_fps_parsing(rate, expected):
    """r_frame_rate는 분수 문자열이다. 분모가 0이거나 파싱에 실패하면 None이다."""
    assert _fps(rate) == expected
