"""변환 산출 검사. 산출물 측정값을 원본과 비교해 위반 코드를 낸다.

워커의 `ok=True`만으로는 산출이 온전한지 알 수 없다. ffmpeg는 입력이 도중에 끊겨도 rc=0으로 끝난다.
예를 들어 앞 절반만 남은 60.0초 원본을 변환하면 디코딩 경고만 찍히고 rc=0으로 30.0초 사본이 나온다.
presigned URL을 스트리밍으로 읽다가 연결이 끊겨도 같은 결과가 된다.
그 사본은 분석(뒷부분 장면이 나오지 않는다)과 라벨링(뒷부분이 재생되지 않는다)까지
그대로 넘어간다.

그래서 역할을 나눈다.

- 워커는 산출물을 한 번 읽어 측정값만 `outputs`에 싣는다(`job_runner._inspect`).
- 받는 쪽(서버)이 이 모듈의 `check_output`으로 원본 probe와 비교해 판정한다.

이 모듈은 서버만 부른다. 판정은 회신을 받는 한 곳에서 한다.
probe 필드(`ProbeResult`)를 다루는 순수 비교라서 워커 패키지에 둔다.
DB와 네트워크를 쓰지 않으므로 이 패키지에서 단위 테스트한다.

위반 코드는 세 가지다.

- `output_unreadable`: 워커가 산출물을 ffprobe로 읽지 못했다.
- `output_check_timeout`: 워커의 ffprobe가 제한 시간 안에 끝나지 않았다.
  원인이 서버 부하일 수 있어 일시 오류로 센다(`TRANSIENT_CAUSES`).
- `output_duration_mismatch`: 원본과의 길이 차이가 `DURATION_TOLERANCE_S`를 넘는다.

측정하지 못한 값은 통과로 둔다. 원본 길이가 없거나 산출 길이가 비어 있으면
비교하지 않고 차이를 `None`으로 둔다. 변환 판정(`_spec_reasons`)과 같은 규칙이다.
값이 없는 경우와 길이가 어긋난 경우를 구분한다. 산출 길이 0은 빈 사본이므로 위반으로 센다.

프레임레이트·코덱·해상도는 싣기만 하고 판정하지 않는다. 판정 기준은 길이 하나다.
세 값은 생산자가 사양으로 정한다. 그래서 어긋나면 입력보다 인코더 결함을 의심한다.
저장해 두면 그 결함을 추적할 수 있다.

컨테이너마다 `format.duration`의 뜻이 달라 원본 길이를 두 가지로 해석할 수 있다.
시작 시각이 있는 원본을 ffmpeg 8.1에서 실제 변환과 같은 사양으로 변환해 측정한 값은 다음과 같다.

- MP4(시작 1.5초): 원본 `format.duration` 20.042(시작 시각을 뺀 길이), 산출 20.053, 차이 +0.011.
- MPEG-TS(시작 1.42초): 원본 20.041(시작 시각을 뺀 길이), 산출 20.053, 차이 +0.012.
- MKV(시작 1.5초): 원본 62.823(시작 시각을 더한 끝 시각), 산출 61.300, 차이 −1.523.

변환은 시작 시각을 0으로 당긴다(`probe.ProbeResult.start_time_seconds` 주석).
그래서 MKV는 산출이 항상 시작 시각만큼 짧아 보인다.
컨테이너별 표를 두면 표에 없는 컨테이너에서 다시 틀린다. 그래서 두 해석 중 산출에 가까운 쪽과
비교한다. 두 해석은 `duration`과 `duration − start_time`이다.
그 대가로 정확히 시작 시각만큼 잘린 사본은 잡지 못한다. 시작 시각은 보통 몇 초 수준이다.
잘린 사본은 대개 남은 길이를 통째로 잃으므로 이 대가는 작다.
"""
from __future__ import annotations

from typing import TypedDict

#: 원본과 산출의 길이 허용 오차(초). 이 값을 넘으면 실패다. 판정 기준값은 이 상수 하나다.
#:
#: 실제 변환과 같은 사양으로 측정한 정상 변환의 차이는 최대 0.023초다.
#: 오디오 재인코딩의 앞머리 지연과 프레임 경계 정렬(29.97fps 한 프레임은 0.033초)에서 생긴다.
#: 잘린 입력의 차이는 −30.05초다. 0.5초는 정상 오차의 스무 배 수준이라
#: 정상 변환은 모두 통과하고 잘린 사본은 걸린다.
DURATION_TOLERANCE_S = 0.5

#: 워커가 산출물을 읽지 못한 깨진 사본.
UNREADABLE = "output_unreadable"
#: 워커의 산출물 검사가 제한 시간을 넘긴 경우. 사본이 깨졌는지는 판정하지 못한 상태다.
CHECK_TIMEOUT = "output_check_timeout"
#: 원본과 길이가 기준보다 크게 다른 사본. 잘렸거나 늘어난 경우다.
DURATION_MISMATCH = "output_duration_mismatch"
#: 시도를 쓰지 않고 다시 도는 위반 코드. 원인이 환경이라 같은 입력을 다시 돌리면 풀릴 수 있다.
#: 일시 오류를 영구 실패로 세면 바쁜 서버일수록 상한에 닿아 실패로 확정된다.
TRANSIENT_CAUSES: frozenset[str] = frozenset({CHECK_TIMEOUT})
#: 시간 초과 표시가 없는 워커가 보내는 시간 초과 원문의 머리말(`probe.ProbeTimeoutError`의 문장).
_TIMEOUT_PREFIX = "ffprobe timeout"


class OutputCheck(TypedDict):
    """산출물 하나의 비교 결과. `cause`가 None이면 통과다."""

    label: str
    #: 위반 코드(`UNREADABLE`·`CHECK_TIMEOUT`·`DURATION_MISMATCH`). 통과면 None이다.
    cause: str | None
    #: 비교에 쓴 원본 길이(초). `duration`과 `duration − start_time` 중 산출에 가까운 쪽이다.
    #: 비교하지 않았으면 None이다.
    expected_seconds: float | None
    #: 산출 길이 − `expected_seconds`(초). 음수면 산출이 짧다. 비교하지 않았으면 None이다.
    delta_seconds: float | None


def _seconds(value: object) -> float | None:
    """값을 초로 읽는다. 숫자가 아니거나 음수면 None을 돌려준다. bool은 숫자로 보지 않는다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value >= 0 else None


def check_output(
    source: dict, output: dict, *, tolerance: float = DURATION_TOLERANCE_S,
) -> OutputCheck:
    """산출물 하나를 원본과 비교한다. 판정 규칙은 이 함수 하나에만 둔다.

    변환(분석본)과 재생본 변환이 같은 함수를 쓴다. 둘 다 원본과 비교한다.
    재생본은 분석본에서 만들지만 사람이 보는 영상이라 원본 길이 전체를 담아야 한다.

    Args:
        source: 원본 probe(`extra["probe"]`). `duration_seconds`·`start_time_seconds`를 쓴다.
        output: 워커가 보낸 산출 측정값 하나(`job_runner._inspect`의 형식).
            `error`가 있으면 워커가 그 파일을 읽지 못한 경우다.
            `timed_out`이 참이면 검사가 제한 시간을 넘긴 경우다.
        tolerance: 길이 허용 오차(초). 테스트가 아니면 기본값(`DURATION_TOLERANCE_S`)을 쓴다.

    Returns:
        OutputCheck: 위반이면 `cause`에 코드가 있다. 길이 값이 없으면 통과로 두고 차이는 None이다.
    """
    label = str(output.get("label") or "")
    if output.get("error"):
        # `timed_out`을 싣지 않는 이전 워커는 시간 초과를 오류 원문으로만 보낸다.
        # 그 원문도 시간 초과로 본다.
        timed_out = bool(output.get("timed_out")) or str(output["error"]).startswith(
            _TIMEOUT_PREFIX)
        return OutputCheck(label=label, cause=CHECK_TIMEOUT if timed_out else UNREADABLE,
                           expected_seconds=None, delta_seconds=None)
    got = _seconds(output.get("duration_seconds"))
    whole = _seconds(source.get("duration_seconds"))
    # 원본 길이 0은 값이 없는 것과 같이 다룬다. 비교할 기준이 없다.
    if got is None or not whole:
        return OutputCheck(label=label, cause=None, expected_seconds=None, delta_seconds=None)
    start = _seconds(source.get("start_time_seconds")) or 0.0
    bases = [whole, whole - start] if 0 < start < whole else [whole]
    expected = min(bases, key=lambda b: abs(got - b))
    delta = round(got - expected, 3)
    return OutputCheck(
        label=label,
        cause=DURATION_MISMATCH if abs(delta) > tolerance else None,
        expected_seconds=round(expected, 3),
        delta_seconds=delta,
    )
