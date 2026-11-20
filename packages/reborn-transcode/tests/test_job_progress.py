"""잡 러너의 진행 보고가 타깃을 넘어 누적되는지 확인한다.

타깃마다 0부터 다시 세면 산출이 둘인 잡(1080p·av1)의 진행 막대가 100%까지 찼다가
0으로 돌아간다. 되돌아가는 막대로는 남은 작업량을 읽을 수 없다.
"""
from __future__ import annotations

import threading
from pathlib import Path

import reborn_transcode.job_runner as jr


def _job(n_targets: int) -> dict:
    """타깃 수만 다른 잡 페이로드를 만든다. 하트비트 주기는 0.01초다."""
    return {
        "video_id": "v", "token": "t", "input_url": "http://x/in.mp4",
        "heartbeat_every_s": 0.01,
        "targets": [
            {"label": f"t{i}", "height": None, "crf": 24,
             "put_url": "http://x/out", "content_type": "video/mp4"}
            for i in range(n_targets)
        ],
    }


def test_heartbeat_seconds_never_go_backwards(monkeypatch, tmp_path: Path):
    """타깃이 둘이어도 보고 값은 줄지 않는다. 두 번째 타깃은 첫 번째가 끝난 값에서 이어진다."""
    gate = threading.Event()

    def _fake(input_uri, output_path, spec, *, on_progress=None, cancel=None, ffmpeg="ffmpeg"):
        """타깃마다 0초부터 10초까지 진행을 보고하는 인코더를 흉내 낸다."""
        # 타깃마다 0초부터 10초까지 보고한다(ffmpeg의 out_time과 같은 기준).
        for sec in (0.0, 5.0, 10.0):
            if on_progress:
                on_progress(sec)
            gate.wait(0.03)
        output_path.write_bytes(b"\x00" * 8)

    monkeypatch.setattr(jr, "transcode_streaming", _fake)
    monkeypatch.setattr(jr, "_put", lambda *a, **k: None)

    beats: list[float] = []
    jr.run_job(_job(2), on_heartbeat=beats.append)

    assert beats, "하트비트가 한 번도 안 왔다"
    assert beats == sorted(beats), f"진행이 되돌아갔다: {beats}"
    # 두 번째 타깃 구간은 첫 타깃 길이(10초)를 넘어선 값으로 보고된다.
    assert max(beats) > 10.0, f"타깃 두 개를 처리했는데 하나 분량만 셌다: {beats}"


def test_single_target_reports_plain_media_seconds(monkeypatch, tmp_path: Path):
    """산출이 하나면 누적 기준점이 0이라 인코딩한 미디어 초를 그대로 보고한다."""
    def _fake(input_uri, output_path, spec, *, on_progress=None, cancel=None, ffmpeg="ffmpeg"):
        """진행을 두 번 보고하고 끝나는 인코더를 흉내 낸다."""
        for sec in (0.0, 7.0):
            if on_progress:
                on_progress(sec)
            threading.Event().wait(0.03)
        output_path.write_bytes(b"\x00" * 8)

    monkeypatch.setattr(jr, "transcode_streaming", _fake)
    monkeypatch.setattr(jr, "_put", lambda *a, **k: None)

    beats: list[float] = []
    jr.run_job(_job(1), on_heartbeat=beats.append)
    assert beats and max(beats) <= 7.0


def test_heartbeats_continue_while_inspecting_and_uploading(monkeypatch, tmp_path: Path):
    """산출 검사와 업로드가 하트비트 주기보다 오래 걸려도 그동안 하트비트가 계속 간다.

    인코딩이 끝난 뒤 하트비트가 끊기면 수 GB 업로드가 리스(5분)를 넘겨 회수되고,
    같은 영상이 새 토큰으로 다시 돈다.
    """
    phase = {"now": "encode"}
    seen: list[str] = []

    def _fake(input_uri, output_path, spec, *, on_progress=None, cancel=None, ffmpeg="ffmpeg"):
        """곧바로 끝나는 인코더를 흉내 낸다."""
        if on_progress:
            on_progress(3.0)
        output_path.write_bytes(b"\x00" * 8)

    def _slow_inspect(label, path):
        """하트비트 주기보다 오래 걸리는 산출 검사를 흉내 낸다."""
        phase["now"] = "inspect"
        threading.Event().wait(0.1)
        return {"label": label}

    def _slow_put(url, path, content_type):
        """하트비트 주기보다 오래 걸리는 업로드를 흉내 낸다."""
        phase["now"] = "upload"
        threading.Event().wait(0.1)

    monkeypatch.setattr(jr, "transcode_streaming", _fake)
    monkeypatch.setattr(jr, "_inspect", _slow_inspect)
    monkeypatch.setattr(jr, "_put", _slow_put)

    def _beat(sec: float) -> None:
        """하트비트가 온 단계를 기록한다."""
        seen.append(phase["now"])

    jr.run_job(_job(1), on_heartbeat=_beat)
    assert "inspect" in seen, f"검사 중 하트비트가 없다: {seen}"
    assert "upload" in seen, f"업로드 중 하트비트가 없다: {seen}"
