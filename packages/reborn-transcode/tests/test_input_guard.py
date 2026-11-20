"""ffmpeg·ffprobe 입력 제한 테스트. 재생목록으로 읽힌 입력이 워커 안에서 다른 주소를 여는지 본다.

명령 인자는 subprocess 없이 검증한다.
실제 ffprobe로 재생목록을 여는 테스트는 ffprobe가 있을 때만 돈다.
"""
from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest
from reborn_transcode import ProbeError, blackness, probe, scene_split
from reborn_transcode.input_guard import MEDIA_DEMUXERS, guard_args, is_playlist
from reborn_transcode.probe import build_command
from reborn_transcode.transcode import TargetSpec, build_h264_command


def _guard_before(cmd: list[str], uri: str) -> list[str]:
    """명령에서 입력 바로 앞의 제한 옵션 네 개를 돌려준다."""
    at = cmd.index(uri)
    start = at - 1 if cmd[at - 1] == "-i" else at
    return cmd[start - 4:start]


@pytest.mark.parametrize("uri, protocols", [
    ("https://x/y.mp4?sig=1", "http,https,tls,tcp"),
    ("/tmp/master.mp4", "file"),
])
def test_every_command_limits_protocols_and_demuxers(uri: str, protocols: str):
    """probe·인코딩·씬 분할 명령이 입력 앞에 프로토콜과 디먹서 제한을 두는지 확인한다."""
    want = ["-protocol_whitelist", protocols, "-format_whitelist", MEDIA_DEMUXERS]
    assert guard_args(uri) == want
    assert _guard_before(build_command(uri), uri) == want
    spec = TargetSpec(label="a", height=720, crf=24)
    h264 = build_h264_command(input_uri=uri, output_path="/tmp/o.mp4", spec=spec)
    assert _guard_before(h264, uri) == want
    assert _guard_before(scene_split.build_command(uri), uri) == want
    assert _guard_before(scene_split.build_command(uri, with_audio=False), uri) == want


def test_luma_and_audio_checks_are_limited_too(monkeypatch):
    """휘도 표본과 오디오 확인이 부르는 명령에도 제한이 붙는지 확인한다."""
    seen: list[list[str]] = []

    def fake_run(cmd, **_):
        """명령을 기록하고 빈 결과를 돌려준다."""
        seen.append(cmd)
        return SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    uri = "https://x/y.mp4"
    blackness._luma_at(uri, 1.0, ffmpeg="ffmpeg", timeout=5)
    scene_split.has_audio(uri)
    for cmd in seen:
        assert _guard_before(cmd, uri) == guard_args(uri), cmd


def test_probe_rejects_playlist_formats(monkeypatch):
    """probe 결과가 재생목록 형식이면 메타를 돌려주지 않고 거절하는지 확인한다."""
    out = {"format": {"format_name": "hls", "duration": "10"},
           "streams": [{"codec_type": "video", "codec_name": "h264"}]}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(out).encode(), stderr=b""))
    with pytest.raises(ProbeError, match="playlist"):
        probe("https://x/y.mp4")
    assert is_playlist("hls") and is_playlist("concat")
    assert not is_playlist("mov,mp4,m4a,3gp,3g2,mj2")


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe가 없다")
def test_real_probe_does_not_follow_a_playlist():
    """URL로 받은 재생목록을 probe해도 목록의 주소로 요청이 없는지 실제 ffprobe로 확인한다."""
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        """재생목록을 내주고, 그 밖의 요청은 경로를 기록한 뒤 404를 돌려준다."""

        def do_GET(self):  # noqa: N802
            """`/list.m3u8`이면 재생목록을, 아니면 404를 돌려준다."""
            if self.path == "/list.m3u8":
                body = ("#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\n"
                        f"http://127.0.0.1:{self.server.server_port}/internal.ts\n"
                        "#EXT-X-ENDLIST\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_):
            """요청 로그를 찍지 않는다."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(ProbeError):
            probe(f"http://127.0.0.1:{server.server_port}/list.m3u8", timeout=20)
    finally:
        server.shutdown()
    assert hits == [], "재생목록의 주소로 요청이 나갔다"
