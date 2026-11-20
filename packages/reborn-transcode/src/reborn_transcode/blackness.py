"""검은 화면 검사. 재생하면 검은 화면만 나오는 영상을 분석 전에 거른다.

MI는 검은 화면만 나오는 영상도 분석에 성공한다.
장면을 나누고 요약도 내지만 결과는 실제 영상과 무관하다.
컨테이너·코덱·해상도·길이가 모두 정상이라 규격 검사(`check_spec`)로는 잡을 수 없다.
이 검사가 없으면 사람이 클립을 열어 본 뒤에야 발견한다. 그때는 이미 분석 비용을 쓴 뒤다.

측정 방법: 균등 간격으로 몇 지점만 짧게 디코딩해 표본마다 평균·최대 휘도를 잰다.
전체를 디코딩하면 47분 영상을 모두 내려받아야 해서 probe를 가볍게 둔 이점이 사라진다.
평균·최대 휘도는 `signalstats`의 `YAVG`·`YMAX`다. ffmpeg가 프레임마다 metadata로 출력한다.

판정 규칙:
  - 표본의 YAVG가 `_LUMA_TH` 미만이고 YMAX가 `_LUMA_MAX_TH` 미만이면 그 지점은 검은 화면이다.
  - 모든 표본이 검은 화면일 때만 위반이다. 일부만 검은 것은 정상이다.
    보도영상에는 시작·끝 페이드, 전환, 암전 연출이 흔하다.
    일부 표본만으로 판정하면 정상 보도영상이 막힌다.
  - 앞뒤 끝은 표본에서 뺀다. 인트로·엔딩 암전은 거의 항상 검게 나와
    정상 영상에도 검은 표본을 만든다.

ffmpeg가 없거나 실패하면 판정하지 않고 `None`을 돌려준다.
읽지 못한 영상을 검은 화면으로 판정하면 도구 문제가 영상 문제로 잘못 표시된다.
"""
from __future__ import annotations

import re
import subprocess

from reborn_transcode.input_guard import guard_args

_DEFAULT_FFMPEG = "ffmpeg"
# 평균 휘도 임계값(0~255).
#
# 기준선은 16이다. 방송용 h264는 limited range(TV range)라서 완전한 검정이 16으로 인코딩된다.
# 검은 화면 영상의 YAVG는 16.0으로 측정된다.
# full range 소스는 검정이 0으로 나오므로 두 경우를 모두 포함해야 한다.
# 임계값이 16 이하이면 limited range의 순수 검정 영상을 통과시킨다.
_LUMA_TH = 20.0
# 최대 휘도 임계값. 평균만 보면 어두운 밤 장면까지 검은 화면으로 판정한다.
# 그래서 가장 밝은 픽셀도 함께 본다. 실제 검은 화면에는 밝은 점이 없다.
# 어두운 실사 영상은 조명·자막·로고 때문에 최댓값이 올라간다.
_LUMA_MAX_TH = 48.0
_SAMPLES = 5           # 표본 수. 늘리면 정확해지지만 디코딩도 그만큼 늘어난다
_WINDOW_S = 1.0        # 표본 하나의 디코딩 길이
_EDGE_SKIP = 0.05      # 앞뒤 5%는 건너뛴다(인트로·엔딩 암전)

_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")
_YMAX_RE = re.compile(r"lavfi\.signalstats\.YMAX=([0-9.]+)")


def sample_luma(
    uri: str, duration_seconds: float, *,
    ffmpeg: str = _DEFAULT_FFMPEG, timeout: int = 120,
    samples: int = _SAMPLES,
) -> list[tuple[float, float]] | None:
    """균등 간격 표본의 (평균, 최대) 휘도 목록을 잰다. 잴 수 없으면 None이다.

    None은 휘도를 측정하지 못했다는 뜻이다. 검은 화면 판정에 쓰지 않는다.

    Args:
        duration_seconds: 표본 지점을 나누는 기준 길이. 없거나 0이면 잴 수 없다.

    Returns:
        list[tuple[float, float]] | None: 표본별 (YAVG, YMAX). 한 지점도 읽지 못했으면 None.
    """
    if not duration_seconds or duration_seconds <= 0:
        return None
    span = duration_seconds * (1 - 2 * _EDGE_SKIP)
    if span <= 0:
        return None
    step = span / (samples + 1)
    out: list[tuple[float, float]] = []
    for i in range(1, samples + 1):
        at = duration_seconds * _EDGE_SKIP + step * i
        y = _luma_at(uri, at, ffmpeg=ffmpeg, timeout=timeout)
        if y is not None:
            out.append(y)
    return out or None


def _luma_at(uri: str, at: float, *, ffmpeg: str, timeout: int) -> tuple[float, float] | None:
    """한 지점의 (평균, 최대) 휘도를 잰다. ffmpeg가 실패하거나 없으면 None이다.

    `-ss`를 입력 앞에 두어 키프레임 seek을 쓴다(디코딩 없이 건너뛴다).
    근처 1초가 검은지만 보면 되므로 seek 위치가 정확하지 않아도 된다.
    """
    cmd = [
        ffmpeg, "-v", "error", "-ss", f"{at:.3f}", *guard_args(uri), "-i", uri,
        "-t", f"{_WINDOW_S}", "-an",
        "-vf", "signalstats,metadata=print:file=-",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.decode("utf-8", "replace")
    avgs = [float(m) for m in _YAVG_RE.findall(text)]
    maxs = [float(m) for m in _YMAX_RE.findall(text)]
    if not avgs:
        return None
    # 최대 휘도는 구간 안에서 가장 밝았던 프레임의 값이다.
    # 한 프레임이라도 그림이 있으면 최댓값이 올라가 검은 화면 판정에서 빠진다.
    return sum(avgs) / len(avgs), max(maxs) if maxs else 255.0


def is_black_video(lumas: list[tuple[float, float]] | None) -> bool:
    """모든 표본이 검은 화면이면 True를 돌려준다. 일부 표본의 암전은 정상으로 본다.

    표본이 검은 화면이려면 평균이 낮고(어둡다) 가장 밝은 픽셀도 낮아야 한다(밝은 점이 없다).
    평균만 보면 어두운 밤 장면이 걸리고, 최댓값만 보면 압축 노이즈 한 점에 판정이 뒤집힌다.
    """
    if not lumas:
        return False
    return all(avg < _LUMA_TH and ymax < _LUMA_MAX_TH for avg, ymax in lumas)
