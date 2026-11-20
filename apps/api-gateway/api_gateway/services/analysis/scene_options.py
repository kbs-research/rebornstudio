"""클립 길이 설정. 스택마다 설정 이름은 다르지만 정하는 값은 같다.

MI는 `sceneRange`(`EXTRA_SHORT`~`LONG`)와 `min/maxSceneDurationSec`로 받는다.
자체 샷 검출은 병합 상한 하나로 받는다.
정하는 값이 같으므로 설정도 하나로 둔다.
설정을 둘로 나누면 클립이 짧은 이유를 찾을 때 확인할 곳이 둘이 된다.

값은 프로젝트를 만들 때 프로젝트 설정에 적고, 요청할 때 쓴 값을 잡에도 기록한다.
그래서 클립을 어떤 기준으로 잘랐는지 잡 기록으로 확인할 수 있다.
보류했다가 다시 보내는 요청은 보류할 때 기록한 값을 쓴다(`resolve`의 `override`).
"""
from typing import Final, Literal

from pydantic import BaseModel, Field, model_validator
from reborn_contracts.clip import CLIP_MIN_SECONDS

#: 프로젝트 `settings` 안의 저장 키. 컬럼 대신 설정에 두어 항목이 늘어도
#: 마이그레이션이 필요 없다.
SETTINGS_KEY: Final = "scene_options"

#: MI가 받는 클립 길이 프리셋. MI 문서와 대조할 수 있게 원값 그대로 보낸다.
SceneRange = Literal["EXTRA_SHORT", "SHORT", "MEDIUM", "LONG"]

#: MI 문서의 허용 범위(초). 벗어난 값은 MI가 400으로 거절하므로 요청 전에 막는다.
MI_MIN_RANGE: Final = (10, 30)
MI_MAX_RANGE: Final = (25, 300)


class SceneOptions(BaseModel):
    """프로젝트의 클립 길이 설정. `None`은 분석기 기본값을 쓴다는 뜻이고 0과 다르다.

    `min_seconds`·`max_seconds`가 `None`이면 값을 보내지 않아 MI가 기본값(15·120)을 쓴다.
    0으로 채우면 0초짜리 클립을 요청하는 값이 된다.

    주의: 이 모델은 한쪽만 정한 값을 허용하고 쌍을 강제하는 검증기를 두지 않는다.
    MI는 하한·상한을 쌍으로만 받지만(`409 code 10098`), 자체 샷 검출 스택은 `max_seconds`
    하나만 병합 상한으로 쓴다. `media/scene_split.py`의 잡 페이로드와 워커
    `reborn_transcode.scene_split.merge()`가 받는 길이 값도 상한 하나다.
    MI 규칙은 요청을 만드는 `to_maiu_body`가 지킨다.
    """

    #: MI 전용 길이 프리셋. 벤더 용어라 자체 샷 검출에는 대응하는 설정이 없고 이 값을 쓰지 않는다.
    scene_range: SceneRange = "MEDIUM"
    #: 클립 하한(초). MI `minSceneDurationSec`.
    min_seconds: int | None = Field(default=None, ge=MI_MIN_RANGE[0], le=MI_MIN_RANGE[1])
    #: 클립 상한(초). MI `maxSceneDurationSec`이면서 자체 샷 검출의 병합 상한.
    #: 두 스택이 같은 뜻으로 쓰는 유일한 값이라 한 필드를 함께 쓴다.
    max_seconds: int | None = Field(default=None, ge=MI_MAX_RANGE[0], le=MI_MAX_RANGE[1])

    @model_validator(mode="after")
    def _min_below_max(self) -> "SceneOptions":
        """하한이 상한보다 크거나 같으면 막는다.

        MI에 보내면 400 응답을 받고 잡이 실패로 남는다.
        그러면 운영자가 실패 이유를 MI 오류 문자열에서 찾아야 한다.
        """
        both = self.min_seconds is not None and self.max_seconds is not None
        if both and self.min_seconds >= self.max_seconds:
            raise ValueError("min_seconds는 max_seconds보다 작아야 합니다")
        return self

    def to_maiu_body(self) -> dict:
        """MI 요청 body 조각을 만든다. 정한 값만 싣는다.

        정하지 않은 값은 보내지 않아 MI 기본값을 따른다.
        자체 기본값으로 채워 보내면 MI가 기본값을 바꿔도 옛 값을 계속 보낸다.

        프리셋과 직접 지정을 함께 보내면 MI가 `409 code 10097`로 거절한다.
        하한·상한 중 한쪽만 보내면 `409 code 10098`로 거절한다.
        그래서 두 값을 모두 정했을 때만 직접 지정 값을 보내고, 아니면 `sceneRange`만 보낸다.
        """
        if self.min_seconds is not None and self.max_seconds is not None:
            # 직접 지정. `sceneRange`를 함께 보내지 않는다.
            return {"minSceneDurationSec": self.min_seconds,
                    "maxSceneDurationSec": self.max_seconds}
        return {"sceneRange": self.scene_range}


def of_project(settings: dict | None) -> SceneOptions:
    """프로젝트의 클립 길이 설정을 돌려준다. 정하지 않았으면 기본값이다.

    저장된 값이 스키마에 맞지 않으면(스키마가 바뀐 뒤의 옛 행) 기본값을 돌려준다.
    여기서 예외를 던지면 설정 하나 때문에 프로젝트 조회 전체가 실패한다.
    """
    raw = (settings or {}).get(SETTINGS_KEY)
    if not isinstance(raw, dict):
        return SceneOptions()
    try:
        return SceneOptions(**raw)
    except ValueError:
        return SceneOptions()


def clip_min_seconds(settings: dict | None) -> int:
    """사람이 넣거나 고치는 구간의 최소 길이(초)를 돌려준다. 클립 길이 하한이 있으면 그 값이다.

    분석이 자르는 클립과 사람이 만드는 클립이 같은 하한을 따라야 한 프로젝트의 클립 길이가 고르다.
    하한을 정하지 않았으면 `CLIP_MIN_SECONDS`다.
    `CLIP_MIN_SECONDS`는 하한 설정이 받는 가장 작은 값과 같다.
    구간 추가·경계 수정의 판정과 화면에 주는 값(`ProjectDTO.clip_min_seconds`)이
    모두 이 함수를 쓴다.
    """
    return of_project(settings).min_seconds or CLIP_MIN_SECONDS


def resolve(project_settings: dict | None, override: SceneOptions | None) -> SceneOptions:
    """이번 요청에 실제로 쓸 값을 정한다. 요청 값이 프로젝트 값보다 우선한다.

    덮어쓰기는 필드별로 병합하지 않고 통째로 바꾼다.
    필드별로 섞으면 잡에 기록한 값과 실제로 보낸 값이 달라진다.
    """
    return override or of_project(project_settings)
