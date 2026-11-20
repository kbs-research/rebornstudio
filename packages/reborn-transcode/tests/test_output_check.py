"""변환 산출 검사의 판정 함수 `check_output`의 경계를 확인한다.

확인하는 규칙은 다섯 가지다. 기준을 넘으면 위반이다. 기준 안이면 통과다.
길이 값이 없으면 통과다. 읽지 못한 산출은 위반이다. 검사 시간 초과는 일시 위반이다.
원본 길이의 두 해석(시작 시각 포함·제외) 중 산출에 가까운 쪽과 비교한다.
수치는 `output_check` 모듈 docstring의 측정값을 그대로 쓴다.
"""
import pytest
from reborn_transcode.output_check import (
    CHECK_TIMEOUT,
    DURATION_MISMATCH,
    DURATION_TOLERANCE_S,
    TRANSIENT_CAUSES,
    UNREADABLE,
    check_output,
)


def _out(duration: float | None, label: str = "analysis-720p") -> dict:
    """워커가 보내는 산출 측정값 하나를 만든다(`job_runner._inspect`의 형식)."""
    return {"label": label, "duration_seconds": duration, "fps": 29.97,
            "video_codec": "h264", "width": 1280, "height": 720, "size_bytes": 1024}


def test_the_tolerance_is_the_decided_half_second():
    """기준값은 0.5초다. 바꾸려면 이 테스트와 근거 docstring을 함께 고친다."""
    assert DURATION_TOLERANCE_S == 0.5


def test_normal_drift_passes_and_carries_its_delta():
    """정상 변환의 오차(−0.001초)는 통과하고 그 차이를 그대로 싣는다."""
    c = check_output({"duration_seconds": 60.026633}, _out(60.026))
    assert c["cause"] is None
    assert c["expected_seconds"] == 60.027
    assert c["delta_seconds"] == -0.001


@pytest.mark.parametrize(("delta", "cause"), [
    (0.5, None),                  # 기준과 같은 값은 통과다
    (-0.5, None),
    (0.501, DURATION_MISMATCH),
    (-0.6, DURATION_MISMATCH),
    (3.0, DURATION_MISMATCH),     # 늘어난 사본도 위반이다. 길이 차이의 크기를 본다
])
def test_the_boundary_is_strictly_greater(delta: float, cause: str | None):
    """기준값과 같으면 통과이고, 넘어야 위반이다."""
    c = check_output({"duration_seconds": 100.0}, _out(100.0 + delta))
    assert c["cause"] == cause


def test_a_truncated_copy_is_caught():
    """앞 절반만 남은 60.0초 원본에서 ffmpeg가 rc=0으로 만든 30.0초 사본을 재현한다."""
    c = check_output({"duration_seconds": 60.026633}, _out(29.973))
    assert c["cause"] == DURATION_MISMATCH
    assert c["delta_seconds"] == pytest.approx(-30.054, abs=1e-3)


def test_an_unreadable_output_is_a_violation():
    """워커가 읽지 못한 산출은 `UNREADABLE` 위반이다."""
    c = check_output({"duration_seconds": 60.0},
                     {"label": "play-480p", "error": "ffprobe failed (rc=1): Invalid data"})
    assert c == {"label": "play-480p", "cause": UNREADABLE,
                 "expected_seconds": None, "delta_seconds": None}


@pytest.mark.parametrize(("source", "duration"), [
    ({}, 60.0),                               # 원본 길이가 없다
    ({"duration_seconds": None}, 60.0),
    ({"duration_seconds": 0}, 60.0),          # 0이면 비교할 기준이 없다
    ({"duration_seconds": "60"}, 60.0),       # 숫자가 아닌 값
    ({"duration_seconds": 60.0}, None),       # 산출 길이가 없다
])
def test_unknown_is_not_a_violation(source: dict, duration: float | None):
    """값이 없으면 길이가 어긋난 경우와 구분해 비교하지 않았다는 사실(차이 None)만 남긴다."""
    c = check_output(source, _out(duration))
    assert c["cause"] is None
    assert c["delta_seconds"] is None and c["expected_seconds"] is None


def test_an_empty_output_is_not_unknown():
    """산출 길이 0은 빈 사본이라 위반이다."""
    assert check_output({"duration_seconds": 60.0}, _out(0.0))["cause"] == DURATION_MISMATCH


def test_the_start_offset_is_read_both_ways():
    """MKV는 시작 시각을 더한 끝 시각을, MP4·TS는 뺀 길이를 돌려준다. 둘 다 통과한다."""
    mkv = check_output({"duration_seconds": 62.823, "start_time_seconds": 1.5}, _out(61.3))
    assert mkv["cause"] is None
    assert mkv["expected_seconds"] == 61.323 and mkv["delta_seconds"] == -0.023

    mp4 = check_output({"duration_seconds": 20.042, "start_time_seconds": 1.5}, _out(20.053))
    assert mp4["cause"] is None
    assert mp4["expected_seconds"] == 20.042 and mp4["delta_seconds"] == 0.011


def test_the_start_offset_does_not_hide_a_truncation():
    """두 해석 중 어느 쪽과 비교해도 잘린 사본은 위반이다."""
    c = check_output({"duration_seconds": 62.823, "start_time_seconds": 1.5}, _out(30.0))
    assert c["cause"] == DURATION_MISMATCH
    assert c["expected_seconds"] == 61.323


@pytest.mark.parametrize("output", [
    {"label": "play-480p", "error": "ffprobe timeout after 120s", "timed_out": True},
    # `timed_out`을 싣지 않는 이전 워커는 오류 원문만 보낸다.
    {"label": "play-480p", "error": "ffprobe timeout after 60s"},
])
def test_a_check_timeout_is_its_own_transient_cause(output: dict):
    """검사 시간 초과는 읽지 못한 사본과 다른 위반이다. 시도를 쓰지 않는 일시 위반으로 센다."""
    c = check_output({"duration_seconds": 60.0}, output)
    assert c["cause"] == CHECK_TIMEOUT
    assert CHECK_TIMEOUT in TRANSIENT_CAUSES
    assert UNREADABLE not in TRANSIENT_CAUSES and DURATION_MISMATCH not in TRANSIENT_CAUSES
