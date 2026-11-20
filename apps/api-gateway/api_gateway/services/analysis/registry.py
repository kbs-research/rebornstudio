"""분석 어댑터 등록부. 어댑터별 능력 선언의 단일 출처다.

능력 선언은 화면, 프로젝트 생성, 계약 테스트, 파이프라인이 함께 읽는다.
선언이 여러 곳에 흩어지면 서로 어긋난다.
예를 들어 화면은 음성 인식이 없다고 표시하는데 파이프라인은 음성 인식 결과를 기다린다.

`MAIU`는 능력 선언만 여기 있고 형식 변환 코드는 `services/maiu`에 있다.

`MAIU`는 `SHOT_BOUNDARIES`를 선언하지 않는다.
MI는 의미 단위로 잘라 보도영상의 클립이 평균 1분 안팎이고, 컷 경계로 쓰기에는 길다.
샷을 함께 선언하면 화면이 컷 경계도 제공한다고 잘못 표시한다.
"""
from reborn_contracts.analysis import AdapterInfo, Capability

from api_gateway.services.analysis import scene_detect

#: MI 어댑터 키. 문자열을 곳곳에 직접 적으면 오타로 생긴 불일치를 찾기 어렵다.
MAIU_KEY = "maiu"

MAIU = AdapterInfo(
    key=MAIU_KEY,
    label="Naver Media Intelligence",
    capabilities=frozenset({
        Capability.SEMANTIC_SEGMENTS,
        Capability.SCENE_SUMMARY,
        Capability.SPEECH_TO_TEXT,
        Capability.SPEAKER_DIARIZATION,
        Capability.KEYWORDS,
    }),
)

#: 샷 경계 어댑터. MI와 다른 능력이다.
#: 같은 영상이 수백 개로 나뉘어 작업 단위 자체가 바뀌므로 MI를 그대로 대체하지 못한다.
SCENEDETECT = scene_detect.INFO

_BY_KEY = {a.key: a for a in (MAIU, SCENEDETECT)}


def get(key: str) -> AdapterInfo | None:
    """키로 어댑터를 찾는다. 등록되지 않은 키면 기본값 대신 `None`을 돌려준다.

    등록되지 않은 스택의 결과를 다른 스택의 능력으로 그리면 화면이 없는 능력을 있다고 표시한다.
    """
    return _BY_KEY.get(key)


def all_adapters() -> list[AdapterInfo]:
    """등록된 어댑터 전부를 돌려준다. 프로젝트 생성 화면이 선택지로 쓴다."""
    return list(_BY_KEY.values())
