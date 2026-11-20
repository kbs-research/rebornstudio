"""비식별화 잡이 쓰는 CPU 스레드 수와 CUDA 대체 경고를 확인한다.

추론 세션과 결과 인코더가 스레드 수를 정하지 않으면 각자 호스트 코어 수만큼 스레드를 만든다.
워커 하나에서 잡 여럿이 돌면 그 스레드들이 CPU 상한을 넘어 서로의 시간을 빼앗는다.
"""

import logging
import subprocess

import pytest
from essential_deident import cpu, detector, video


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """스레드와 장치 환경변수를 지운다."""
    for key in ("DEIDENT_THREADS", "GPU_WORKER_CONCURRENCY", "ESSENTIAL_DEIDENT_DEVICE"):
        monkeypatch.delenv(key, raising=False)


def test_job_threads_divides_the_cpu_limit_by_concurrency(monkeypatch):
    """명시 값이 없으면 CPU 상한을 동시 실행 수로 나눈다. 명시 값이 있으면 그 값을 쓴다."""
    monkeypatch.setattr(cpu, "available_cpus", lambda: 4)
    assert cpu.job_threads() == 2
    monkeypatch.setenv("GPU_WORKER_CONCURRENCY", "8")
    assert cpu.job_threads() == 1, "최소 1이어야 한다"
    monkeypatch.setenv("DEIDENT_THREADS", "3")
    assert cpu.job_threads() == 3
    monkeypatch.setenv("DEIDENT_THREADS", "")
    assert cpu.job_threads() == 1, "빈 값은 지정하지 않은 것과 같다"


def test_cgroup_limit_wins_over_the_host_core_count(monkeypatch, tmp_path):
    """cgroup v2 CPU 상한이 있으면 호스트 코어 수 대신 그 값을 쓴다."""
    limit = tmp_path / "cpu.max"
    limit.write_text("300000 100000\n")
    monkeypatch.setattr(cpu, "_CGROUP_V2", limit)
    assert cpu.available_cpus() == 3
    limit.write_text("max 100000\n")
    monkeypatch.setattr(cpu, "_CGROUP_V1", (tmp_path / "none-q", tmp_path / "none-p"))
    assert cpu.available_cpus() >= 1


def test_session_options_bind_threads_and_stop_spinning():
    """추론 세션은 연산 내부 스레드를 잡 몫으로, 연산 사이 스레드를 1로 두고 대기 회전을 끈다."""
    opts = detector.session_options(2)
    assert (opts.intra_op_num_threads, opts.inter_op_num_threads) == (2, 1)
    assert opts.get_session_config_entry("session.intra_op.allow_spinning") == "0"


def test_writer_binds_encoder_and_filter_threads(monkeypatch, tmp_path):
    """결과 인코더 명령의 출력 쪽에 스레드 수와 필터 스레드 수가 들어간다."""
    seen: list[list[str]] = []

    class _Proc:
        """명령만 기록하는 프로세스 대역."""
        stdin = stderr = None

    monkeypatch.setenv("DEIDENT_THREADS", "2")
    monkeypatch.setenv("ESSENTIAL_DEIDENT_ENCODER", "libx264")
    monkeypatch.setattr(video.subprocess, "Popen",
                        lambda cmd, **kw: (seen.append(cmd), _Proc())[1])
    info = video.VideoInfo(fps=30.0, fps_text="30/1", has_audio=False, duration_s=1.0)
    video.Writer(str(tmp_path / "o.mp4"), str(tmp_path / "i.mp4"), 64, 64, info)
    cmd = seen[0]
    after = cmd[cmd.index("-i") + 2:]
    assert after[after.index("-threads") + 1] == "2"
    assert cmd[cmd.index("-filter_threads") + 1] == "2"


def test_cpu_fallback_is_warned_and_reported(monkeypatch, caplog):
    """CUDA를 기대했는데 CPU로 돌면 경고를 남기고 결과 보고의 `notices`에 싣는다."""
    monkeypatch.setenv("ESSENTIAL_DEIDENT_DEVICE", "cuda")
    with caplog.at_level(logging.WARNING, logger="essential_deident.detector"):
        det = detector.FaceDetector()
    assert det.device == "cpu" and det.cpu_fallback is True
    assert any("CPU로 돕니다" in r.getMessage() for r in caplog.records)

    monkeypatch.setenv("ESSENTIAL_DEIDENT_DEVICE", "cpu")
    assert detector.FaceDetector().cpu_fallback is False, "CPU를 고른 경우는 대체가 아니다"


def test_report_carries_device_and_fallback_notice(monkeypatch, tmp_path):
    """결과 보고에 실제 장치와 CPU 대체 표시가 실린다. 서버가 그대로 단계 이력에 남긴다."""
    from essential_deident import pipeline

    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=160x120:rate=10:duration=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    monkeypatch.setenv("ESSENTIAL_DEIDENT_DEVICE", "cuda")
    monkeypatch.setenv("ESSENTIAL_DEIDENT_ENCODER", "libx264")
    report = pipeline.anonymize(str(src), str(tmp_path / "out.mp4"))
    assert report["device"] == "cpu"
    assert report["notices"] == ["cpu-fallback"]
