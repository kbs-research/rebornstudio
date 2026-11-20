"""reborn-transcode 인코딩 명령의 단위 테스트. ffmpeg를 실행하지 않는다."""
from __future__ import annotations

from pathlib import Path

import pytest
from reborn_transcode import transcode
from reborn_transcode.transcode import TargetSpec, TranscodeError, build_h264_command


def test_command_carries_preset_and_threads():
    """프리셋·스레드는 사양에서 온다. 워커가 임의로 정하지 않는다."""
    cmd = build_h264_command(
        input_uri="http://x/in.mp4", output_path=Path("/tmp/o.mp4"),
        spec=TargetSpec("play-480p", 480, 26, preset="veryfast", threads=2), nvenc=False)
    assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "veryfast"
    # -threads는 입력 앞에 와야 디코딩에도 적용된다.
    assert cmd.index("-threads") < cmd.index("-i")
    assert cmd[cmd.index("-threads") + 1] == "2"
    # 출력 쪽에도 같은 수를 둔다.
    # 입력 앞에만 두면 인코더와 필터가 호스트 코어 수만큼 스레드를 만든다.
    after = cmd[cmd.index("-i") + 2:]
    assert "-threads" in after and after[after.index("-threads") + 1] == "2"
    assert cmd[cmd.index("-filter_threads") + 1] == "2"


def test_threads_zero_means_ffmpeg_auto():
    """0이면 `-threads` 플래그를 붙이지 않는다.

    `-threads 0`을 명시해도 뜻은 같다. 플래그를 붙이지 않아야 이전 잡과 다른 호출자가
    기대하는 명령 형태가 유지된다.
    """
    cmd = build_h264_command(
        input_uri="http://x/in.mp4", output_path=Path("/tmp/o.mp4"),
        spec=TargetSpec("analysis-720p", 720, 24), nvenc=False)
    assert "-threads" not in cmd and "-filter_threads" not in cmd
    assert cmd[cmd.index("-preset") + 1] == "fast"


def test_nvenc_command_maps_preset_and_quality():
    """NVENC면 프리셋을 p 단계로 바꾸고 crf 값을 고정 품질로 쓰며 8비트로 맞춘다."""
    cmd = build_h264_command(
        input_uri="http://x/in.mp4", output_path=Path("/tmp/o.mp4"),
        spec=TargetSpec("play-480p", 480, 26, preset="veryfast", threads=2), nvenc=True)
    assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
    assert cmd[cmd.index("-preset") + 1] == "p2"
    assert cmd[cmd.index("-cq") + 1] == "26"
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert "libx264" not in cmd and "-crf" not in cmd
    assert cmd.index("-threads") < cmd.index("-i")


def test_encoder_env_overrides_probe(monkeypatch):
    """`TRANSCODE_ENCODER`로 고르면 시험 인코딩 없이 그 인코더를 쓴다."""
    def fail(_ffmpeg):
        """불리면 실패한다."""
        raise AssertionError("시험 인코딩을 하지 않아야 한다")

    monkeypatch.setattr(transcode, "_nvenc_works", fail)
    monkeypatch.setenv("TRANSCODE_ENCODER", "libx264")
    assert transcode._use_nvenc("ffmpeg") is False
    monkeypatch.setenv("TRANSCODE_ENCODER", "nvenc")
    assert transcode._use_nvenc("ffmpeg") is True


def test_nvenc_failure_retries_with_libx264(monkeypatch):
    """NVENC 인코딩이 실패하면 같은 사양을 libx264로 한 번 더 인코딩한다."""
    calls: list[list[str]] = []

    def fake_run(cmd, spec, on_progress, cancel):
        """첫 실행(NVENC)만 실패한다."""
        calls.append(cmd)
        if len(calls) == 1:
            raise TranscodeError("ffmpeg play-480p failed (rc=1): OpenEncodeSessionEx failed")

    monkeypatch.setattr(transcode, "_use_nvenc", lambda _ffmpeg: True)
    monkeypatch.setattr(transcode, "_run", fake_run)
    transcode.transcode_streaming("http://x/in.mp4", Path("/tmp/o.mp4"),
                                  TargetSpec("play-480p", 480, 26))
    assert [c[c.index("-c:v") + 1] for c in calls] == ["h264_nvenc", "libx264"]


def test_cancel_is_not_retried(monkeypatch):
    """리스 상실이나 진행 정체로 멈춘 인코딩은 다른 인코더로 다시 하지 않는다."""
    calls: list[list[str]] = []

    def fake_run(cmd, spec, on_progress, cancel):
        """취소로 끝난다."""
        calls.append(cmd)
        raise transcode._CancelledError("play-480p 취소됨 (리스 상실 또는 진행 정체)")

    monkeypatch.setattr(transcode, "_use_nvenc", lambda _ffmpeg: True)
    monkeypatch.setattr(transcode, "_run", fake_run)
    with pytest.raises(TranscodeError, match="취소됨"):
        transcode.transcode_streaming("http://x/in.mp4", Path("/tmp/o.mp4"),
                                      TargetSpec("play-480p", 480, 26))
    assert len(calls) == 1


@pytest.mark.parametrize("stderr", [
    "[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: out of memory (10)",
    "[h264_nvenc @ 0x1] No capable devices found",
    "[hevc_cuvid @ 0x1] CUDA_ERROR_OUT_OF_MEMORY",
])
def test_nvenc_errors_are_retried_with_libx264(monkeypatch, stderr):
    """stderr가 NVENC·CUDA 오류이면 libx264로 다시 인코딩한다."""
    calls: list[list[str]] = []

    def fake_run(cmd, spec, on_progress, cancel):
        """첫 실행(NVENC)만 주어진 stderr로 실패한다."""
        calls.append(cmd)
        if len(calls) == 1:
            raise TranscodeError("ffmpeg play-480p failed (rc=1): Conversion failed!",
                                 stderr=stderr)

    monkeypatch.setattr(transcode, "_use_nvenc", lambda _ffmpeg: True)
    monkeypatch.setattr(transcode, "_run", fake_run)
    transcode.transcode_streaming("http://x/in.mp4", Path("/tmp/o.mp4"),
                                  TargetSpec("play-480p", 480, 26))
    assert len(calls) == 2


@pytest.mark.parametrize("stderr", [
    "http://x/in.mp4?…: Server returned 403 Forbidden",
    "Invalid data found when processing input",
    "Error while decoding stream #0:0: Invalid data found when processing input",
])
def test_unrelated_failures_are_not_reencoded(monkeypatch, stderr):
    """입력이나 네트워크 실패는 인코더와 무관하다. 같은 입력을 CPU로 다시 인코딩하지 않는다."""
    calls: list[list[str]] = []

    def fake_run(cmd, spec, on_progress, cancel):
        """주어진 stderr로 실패한다."""
        calls.append(cmd)
        raise TranscodeError("ffmpeg play-480p failed (rc=1)", stderr=stderr)

    monkeypatch.setattr(transcode, "_use_nvenc", lambda _ffmpeg: True)
    monkeypatch.setattr(transcode, "_run", fake_run)
    with pytest.raises(TranscodeError):
        transcode.transcode_streaming("http://x/in.mp4", Path("/tmp/o.mp4"),
                                      TargetSpec("play-480p", 480, 26))
    assert len(calls) == 1, "인코더와 무관한 실패를 libx264로 다시 인코딩했다"


def test_run_keeps_the_redacted_stderr(monkeypatch, tmp_path):
    """ffmpeg가 실패하면 서명을 지운 stderr를 예외에 싣는다. 재시도 판정이 그 원문을 읽는다."""
    script = tmp_path / "ffmpeg"
    script.write_text("#!/bin/sh\n"
                      "echo 'http://h/k?X-Amz-Signature=abc: No capable devices found' >&2\n"
                      "exit 1\n")
    script.chmod(0o755)
    with pytest.raises(TranscodeError) as exc:
        transcode._run([str(script)], TargetSpec("t", None, 24), None, None)
    assert "No capable devices" in exc.value.stderr
    assert "Signature" not in exc.value.stderr and "Signature" not in str(exc.value)
