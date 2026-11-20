"""썸네일 프레임 추출이 입력을 영상 컨테이너와 http(s)로 제한하는지 확인하는 테스트.

Content-Type이 HLS인 파일을 ffmpeg가 재생목록으로 읽으면 목록에 적힌 주소를 워커 안에서 연다.
"""
import subprocess
from types import SimpleNamespace

import pytest
from api_gateway.services.media import clip_thumbnail
from reborn_transcode.input_guard import guard_args


def test_extract_frame_limits_the_input(monkeypatch):
    """프레임 추출 명령의 입력 앞에 프로토콜·디먹서 제한이 붙는지 확인한다."""
    seen: list[list[str]] = []

    def fake_run(cmd, **_):
        """명령을 기록하고 실패 결과를 돌려준다."""
        seen.append(cmd)
        return SimpleNamespace(returncode=1, stderr=b"Invalid data found when processing input")

    monkeypatch.setattr(subprocess, "run", fake_run)
    url = "https://bucket.example/intake/x/source?X-Amz-Signature=s"
    with pytest.raises(clip_thumbnail.FrameError):
        clip_thumbnail._extract_frame(url, 3.0)
    cmd = seen[0]
    at = cmd.index("-i")
    assert cmd[at - 4:at] == guard_args(url)
