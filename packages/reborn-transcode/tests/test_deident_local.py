"""비식별화 잡이 내장 모듈로 영상을 가리고 결과와 좌표를 올리는지 확인한다."""

import json
import subprocess
from pathlib import Path

import pytest
import reborn_transcode.deident as dd
from reborn_transcode.job_runner import JobError


@pytest.fixture
def clip(tmp_path):
    """오디오가 있는 1초짜리 합성 영상을 만든다."""
    path = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=30:duration=1", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


class _Stream:
    """httpx.stream 대신 로컬 파일을 내려 주는 응답."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        """항상 성공 응답으로 본다."""

    def iter_bytes(self):
        """파일 내용을 한 번에 돌려준다."""
        yield self.path.read_bytes()


def test_local_job_uploads_video_and_coordinates(monkeypatch, clip, tmp_path):
    """좌표 주소가 있는 잡은 결과 영상과 좌표 파일을 올리고 보고에 좌표 설명을 싣는다."""
    monkeypatch.delenv("DEIDENT_MODEL", raising=False)
    monkeypatch.setattr(dd.httpx, "stream", lambda *a, **k: _Stream(clip))
    puts: dict[str, bytes] = {}
    monkeypatch.setattr(dd, "_put", lambda url, path, ctype: puts.__setitem__(
        url, (ctype, Path(path).read_bytes())))
    job = {"video_id": "v", "token": "t", "input_url": "http://in", "put_url": "http://out",
           "coordinates_put_url": "http://coords", "content_type": "video/mp4"}
    result = dd.run_deident_job(job)
    report = result["report"]
    assert report["model"] == "essential-yunet" and report["frames"] == 30
    assert report["coordinates"]["schema_version"] == 1
    assert puts["http://out"][0] == "video/mp4" and len(puts["http://out"][1]) > 1000
    ctype, body = puts["http://coords"]
    assert ctype == "application/json" and json.loads(body)["frame_count"] == 30


def test_local_job_without_coordinates_url_skips_coordinates(monkeypatch, clip):
    """좌표 주소가 없으면 좌표 파일을 쓰지도 올리지도 않는다."""
    monkeypatch.setattr(dd.httpx, "stream", lambda *a, **k: _Stream(clip))
    puts: list[str] = []
    monkeypatch.setattr(dd, "_put", lambda url, path, ctype: puts.append(url))
    job = {"video_id": "v", "token": "t", "input_url": "http://in", "put_url": "http://out"}
    report = dd.run_deident_job(job)["report"]
    assert puts == ["http://out"] and "coordinates" not in report


def test_unknown_model_fails_permanently(monkeypatch):
    """등록되지 않은 모델 이름이면 입력을 받기 전에 영구 실패한다."""
    monkeypatch.setenv("DEIDENT_MODEL", "no-such-model")
    job = {"video_id": "v", "token": "t", "input_url": "http://in", "put_url": "http://out"}
    with pytest.raises(JobError) as e:
        dd.run_deident_job(job)
    assert e.value.transient is False


def test_default_model_masks():
    """모델 설정이 없을 때 쓰는 기본 모델은 마스킹하는 내장 YuNet 어댑터다."""
    assert dd.DEFAULT_MODEL == "essential-yunet"
    assert dd._ADAPTERS[dd.DEFAULT_MODEL] is dd._essential_yunet


def test_heartbeats_continue_while_uploading(monkeypatch, tmp_path):
    """비식별화 결과를 올리는 동안에도 하트비트가 계속 간다.

    큰 결과 파일은 업로드만으로 리스(5분)를 넘긴다.
    """
    src = tmp_path / "src.mp4"
    src.write_bytes(b"\x00" * 16)
    monkeypatch.setattr(dd.httpx, "stream", lambda *a, **k: _Stream(src))
    monkeypatch.setitem(dd._ADAPTERS, "essential-yunet",
                        lambda s, d, c=None: (d.write_bytes(b"x"), {"model": "m"})[1])
    monkeypatch.delenv("DEIDENT_MODEL", raising=False)
    uploading = {"on": False}
    during: list[bool] = []

    def _slow_put(url, path, ctype):
        """하트비트 주기보다 오래 걸리는 업로드를 흉내 낸다."""
        uploading["on"] = True
        __import__("time").sleep(0.1)
        uploading["on"] = False

    monkeypatch.setattr(dd, "_put", _slow_put)
    job = {"video_id": "v", "token": "t", "input_url": "http://in", "put_url": "http://out",
           "heartbeat_every_s": 0.01}
    dd.run_deident_job(job, on_heartbeat=lambda _sec: during.append(uploading["on"]))
    assert True in during, "업로드 중 하트비트가 없다"
