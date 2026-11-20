"""샷 검출과 병합 어댑터. `SHOT_BOUNDARIES` 능력을 구현한다.

더미 어댑터와 구조가 다른 구현이라 계약이 실제 분석기를 수용하는지도 이 어댑터로 확인한다.
이 어댑터가 계약 테스트를 통과하면 계약이 유효하다.

MI와는 다른 능력이다.
- MI는 의미 단위(기사·리포트)로 자르고, 보도영상의 클립이 평균 1분 안팎이다.
- 이 어댑터는 컷(샷)을 찾아 묶는다. 컷은 2~5초마다 바뀌므로 묶어서 40초 이하로 만든다.
묶은 구간은 MI의 기사 경계와 일부만 겹친다.
그래서 능력 이름은 `SHOT_BOUNDARIES`로 두고 규칙 문구에도 "의미 단위"라고 쓰지 않는다.

이 모듈은 엔진 결과를 계약 타입으로 옮기기만 한다.
검출과 병합은 원본 바이트를 읽어야 해서 워커가 실행하고,
구현은 `reborn_transcode.scene_split`(`packages/reborn-transcode/`)에 있다.
구현을 이 모듈에 두면 워커가 api_gateway에 의존하게 된다.

검출에는 PySceneDetect 대신 FFmpeg `gt(scene,0.3)`을 쓴다.
PySceneDetect는 처리 시간이 약 3.3배이고 opencv 의존성(약 145MB)이 더 필요하다.
검출 함수는 `analyze`의 `split` 인자로 바꿀 수 있다.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from reborn_contracts.analysis import (
    AdapterInfo,
    AnalysisResult,
    Capability,
    Segment,
    SegmentBasis,
)
from reborn_transcode import scene_split as engine

INFO = AdapterInfo(
    key="scenedetect",
    label="샷 경계 검출 (FFmpeg)",
    # 샷 경계 하나만 선언한다. 요약과 음성 인식은 만들지 않는다.
    # 없는 능력을 선언하면 화면이 "있음"이라 표시한 뒤 결과를 보여 주지 못한다
    # (계약 테스트가 잡아낸다).
    capabilities=frozenset({Capability.SHOT_BOUNDARIES}),
)

# 엔진의 값을 그대로 쓴다. 여기서 다시 정의하면 두 곳의 값이 어긋난다.
# 그러면 화면이 표시하는 규칙과 실제로 자른 규칙이 달라진다.
DEFAULT_THRESHOLD = engine.DEFAULT_THRESHOLD
MERGE_MAX_SEC = engine.MERGE_MAX_SEC
SILENCE_MIN_SEC = engine.SILENCE_MIN_SEC

#: 엔진 호출 함수의 시그니처. `(입력, 길이, 임계)`를 받아 `run_job` 결과 dict를 돌려준다.
Splitter = Callable[[str, float, float], dict]


def _default_split(uri: str, duration: float, threshold: float) -> dict:
    """기본 엔진(`engine.run_job`)으로 샷을 검출하고 병합한다."""
    return engine.run_job({
        "input_url": uri, "duration_seconds": duration, "threshold": threshold,
    })


def to_result(payload: dict) -> AnalysisResult:
    """엔진 결과 dict를 도메인 모델로 옮긴다.

    엔진 응답 형식이 도메인 안까지 들어오지 않게 한다.
    선언한 능력만 채운다. 이 스택은 요약·발화·키워드를 만들지 않으므로
    `summary`·`speeches`·`keywords`를 비운다.

    `basis`에는 묶기 전 개수와 규칙을 담는다.
    병합 결과 수만 주면 그 수가 많은지 판단할 수 없어 운영자가 임계를 조정할 근거가 없다.
    """
    segs = [Segment(start=s["start"], end=s["end"]) for s in payload.get("segments", [])]
    if not segs:
        return AnalysisResult(segments=[])
    return AnalysisResult(
        segments=segs,
        basis=SegmentBasis(
            raw_count=int(payload.get("raw_count") or len(segs)),
            merged_count=len(segs),
            rule=payload.get("rule") or engine.rule_text(),
        ),
    )


def analyze(
    *,
    path: Path | str,
    duration_seconds: float,
    threshold: float = DEFAULT_THRESHOLD,
    split: Splitter | None = None,
) -> AnalysisResult:
    """샷을 찾아 병합한 구간을 돌려준다. 컷 단위 결과는 돌려주지 않는다.

    Args:
        path: 로컬 경로 또는 presigned URL. ffmpeg는 둘을 같은 방식으로 읽는다.
        split: 엔진을 대신할 함수. 테스트는 여기에 스텁을 넣어 바이너리 없이
            계약을 검증한다. 계약 테스트가 실행 환경에 의존하면
            계약 준수 대신 환경 구성 여부를 검사하게 된다.
    """
    return to_result((split or _default_split)(str(path), duration_seconds, threshold))
