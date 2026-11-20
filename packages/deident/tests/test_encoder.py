"""결과 영상 인코더 선택을 확인한다. NVENC는 실제로 인코딩될 때만 고른다."""

import subprocess

import pytest
from essential_deident import video


@pytest.fixture(autouse=True)
def _fresh_probe(monkeypatch):
    """확인 결과 캐시를 비우고 인코더 환경변수를 지운다."""
    monkeypatch.delenv("ESSENTIAL_DEIDENT_ENCODER", raising=False)
    video._nvenc_works.cache_clear()
    yield
    video._nvenc_works.cache_clear()


def test_falls_back_when_nvenc_encode_fails(monkeypatch):
    """ffmpeg에 h264_nvenc가 있어도 시험 인코딩이 실패하면 libx264를 고른다."""
    calls = []

    def fake_run(cmd, **kwargs):
        """NVENC 시험 인코딩을 실패로 돌려준다."""
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, b"", b"No NVENC capable devices found")

    monkeypatch.setattr(video.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(video.subprocess, "run", fake_run)
    assert video._encoder()[:2] == ["-c:v", "libx264"]
    # 확인은 프로세스마다 한 번이다. 잡마다 시험 인코딩을 다시 하지 않는다.
    video._encoder()
    assert len(calls) == 1 and "h264_nvenc" in calls[0]


def test_uses_nvenc_when_encode_succeeds(monkeypatch):
    """시험 인코딩이 성공하면 NVENC를 고른다."""
    monkeypatch.setattr(video.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(video.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, b"", b""))
    assert video._encoder()[:2] == ["-c:v", "h264_nvenc"]


def test_env_overrides_probe(monkeypatch):
    """환경변수로 고르면 시험 인코딩 없이 그 인코더를 쓴다."""
    def fail(*_a, **_k):
        """불리면 실패한다."""
        raise AssertionError("시험 인코딩을 하지 않아야 한다")

    monkeypatch.setattr(video.subprocess, "run", fail)
    monkeypatch.setenv("ESSENTIAL_DEIDENT_ENCODER", "libx264")
    assert video._encoder()[:2] == ["-c:v", "libx264"]
    monkeypatch.setenv("ESSENTIAL_DEIDENT_ENCODER", "nvenc")
    assert video._encoder()[:2] == ["-c:v", "h264_nvenc"]
