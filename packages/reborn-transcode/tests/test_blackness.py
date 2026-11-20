"""검은 화면 검사의 단위 테스트. CI에 ffmpeg가 없어서 subprocess를 흉내 낸다."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

from reborn_transcode.blackness import is_black_video, sample_luma


def _proc(pairs: list[tuple[float, float]], rc: int = 0):
    """ffmpeg `signalstats` 출력을 흉내 낸 실행 결과를 만든다."""
    out = "\n".join(
        f"lavfi.signalstats.YAVG={a}\nlavfi.signalstats.YMAX={m}" for a, m in pairs
    ).encode()
    return SimpleNamespace(returncode=rc, stdout=out, stderr=b"")


def test_sample_luma_spreads_points_and_skips_edges(monkeypatch):
    """표본은 앞뒤 끝을 피해 균등 간격으로 뽑는다. 인트로 암전이 검은 표본으로 잡히지 않게 한다."""
    seen: list[float] = []

    def fake_run(cmd, **kw):
        """`-ss` 위치를 기록하고 밝은 표본을 돌려준다."""
        seen.append(float(cmd[cmd.index("-ss") + 1]))
        return _proc([(100.0, 240.0)])

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = sample_luma("url", 100.0, samples=4)
    assert out == [(100.0, 240.0)] * 4
    assert all(5.0 < t < 95.0 for t in seen), seen      # 앞뒤 5%를 뺀 구간 안에 있다
    assert seen == sorted(seen) and len(set(seen)) == 4  # 서로 다른 지점, 순서대로


def test_sample_luma_returns_none_when_unmeasurable(monkeypatch):
    """길이가 없거나 ffmpeg가 없으면 None을 돌려준다. 검은 화면으로 판정하지 않는다."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc([(0.0, 0.0)]))
    assert sample_luma("url", 0.0) is None

    def boom(*a, **k):
        """ffmpeg가 없는 환경을 흉내 낸다."""
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(subprocess, "run", boom)
    assert sample_luma("url", 100.0) is None


def test_is_black_video_needs_every_sample_black():
    """모든 표본이 검을 때만 위반이다. 일부 암전(페이드·전환)은 정상이다."""
    black = (16.0, 16.0)
    assert is_black_video([black] * 5) is True
    assert is_black_video([black, black, (90.0, 240.0), black, black]) is False
    assert is_black_video([]) is False
    assert is_black_video(None) is False                          # 측정하지 못한 영상은 통과다


def test_limited_range_black_is_16_not_0():
    """limited range(TV range)의 검정은 16이다.

    방송용 h264가 limited range다. 순수 검정 영상의 YAVG는 정확히 16.0이다.
    임계값이 16보다 낮으면 그 영상을 통과시킨다.
    """
    assert is_black_video([(16.0, 16.0)] * 5) is True


def test_dark_scene_with_a_highlight_is_not_black():
    """평균이 검정과 같아도 밝은 점이 하나라도 있으면 통과다.

    검정 배경에 8×8 흰 사각형 하나를 넣은 영상은 YAVG가 16.06으로 순수 검정(16.0)과
    거의 같지만 YMAX는 236이다. 평균만 보면 이 영상을 거부한다.
    """
    assert is_black_video([(16.06, 236.0)] * 5) is False
