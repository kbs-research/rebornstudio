"""분석 스택 프리셋 카탈로그. 스토리지와 분석기의 조합은 코드가 가진다.

운영자는 정해진 조합 중에서만 고른다.
스토리지와 분석기를 자유롭게 짝지으면 동작하지 않는 조합이 생긴다.
예를 들어 MI가 읽지 못하는 버킷과 짝지으면 등록은 성공하고 분석만 실패한다.
그래서 조합은 코드가 가지고, 값(엔드포인트·버킷·자격증명)만 운영자가 채운다.
새 조합을 더하려면 코드를 고쳐 배포한다.

연결이 없어 지금 쓸 수 없는 프리셋도 목록에 둔다.
목록에서 빼면 운영자는 그 조합을 쓸 수 없다는 사실을 모른 채 계속 찾는다.
`available=False`로 보여 주고 먼저 등록할 연결을 적는다(`_with_readiness`).

프리셋은 프로젝트 생성 시 고정된다.
분석기를 바꾸면 한 프로젝트에 두 분석기의 결과가 섞이고, 라벨러는 그 차이를 모른 채 작업한다.
그래서 프리셋은 바꿀 수 없고, 화면은 나중에 바꿀 수 없다는 사실을 알린다.

프리셋이 고정되어도 클립에는 출처를 남긴다.
한 프로젝트 안에서도 장면 분할은 자체 구현이, 요약은 외부 분석기가 맡을 수 있다.
그래서 요약마다 어느 스택이 만들었는지 기록해야 한다.
"""
from typing import Final

from pydantic import BaseModel

from api_gateway.config import settings as app_settings
from api_gateway.services import platform_credentials
from api_gateway.services.analysis import registry


class AnalysisPreset(BaseModel):
    """분석 스택 하나. 무엇을 쓰는지와 지금 쓸 수 있는지를 담는다.

    `available=False`여도 목록에 두고 이유와 함께 보여 준다.
    목록에서 빼면 운영자는 그 스택을 쓸 수 없다는 사실을 알 수 없다.
    """

    key: str
    label: str
    #: 원본과 산출물을 저장하는 스토리지 키. 분석기와 짝이 맞아야 한다.
    #: 외부 분석기는 대개 같은 클라우드의 버킷만 읽으므로 그 제약을 코드에 둔다.
    storage_key: str
    #: 분석을 맡는 어댑터의 `registry` 키.
    adapter_key: str
    #: 장면을 자르는 어댑터 키. `None`이면 분석기가 분석하면서 자른다(MI).
    scene_adapter_key: str | None = None
    description: str = ""
    available: bool = True
    #: 쓸 수 없는 이유. `available=False`면 반드시 채운다.
    #: 이유 없이 비활성으로만 보이면 운영자가 다음에 할 일을 알 수 없다.
    unavailable_reason: str | None = None
    #: 등록한 연결이 없어 쓸 수 없을 때 먼저 등록해야 하는 연결의 서비스(`ncp`·`maiu`).
    missing_providers: list[str] = []

    # `description`은 화면에 그대로 나가는 제품 문구다.
    # 나란히 놓고 고르는 화면이라 설명을 같은 구조로 쓴다.
    #   첫 문장: 나누는 방식과 캡션 초안을 만드는 주체
    #   둘째 문장: 분석 비용이 드는지(`분석 비용이 들고`·`분석 비용이 들지 않고`)와 그 결과
    # 종결은 `~합니다`로 쓴다. 용어는 화면의 기준 용어 `캡션`을 쓰고 `STT`·`자막`은 쓰지 않는다.
    # 마크다운과 1인칭은 쓰지 않는다.


NCP_MI: Final = AnalysisPreset(
    key="ncp-mi",
    label="NCP + Media Intelligence",
    storage_key="ncp",
    adapter_key=registry.MAIU_KEY,
    # MI는 분석하면서 의미 단위로 자르므로 별도 분할 단계가 없다.
    scene_adapter_key=None,
    description=(
        "영상을 의미 단위로 나누고 캡션 초안까지 만듭니다. "
        "분석 비용이 들고 작업자는 초안을 고칩니다."
    ),
)

NCP_SHOTS: Final = AnalysisPreset(
    key="ncp-shots",
    label="NCP + 샷 검출",
    storage_key="ncp",
    # 별도 분석기가 없고 이 스택은 구간 분할만 한다. 요약과 캡션은 사람이 쓴다.
    adapter_key=registry.SCENEDETECT.key,
    scene_adapter_key=registry.SCENEDETECT.key,
    description=(
        "샷이 바뀌는 지점을 찾아 최대 40초 단위로 나눕니다. "
        "분석 비용이 들지 않고 작업자가 캡션을 처음부터 씁니다."
    ),
)

_ALL: Final = (NCP_MI, NCP_SHOTS)

#: 선택하지 않았을 때 쓰는 프리셋 키. 설정이 없는 프로젝트도 이 조합으로 동작한다.
DEFAULT_KEY: Final = NCP_MI.key

#: 프로젝트 설정 안의 저장 키. 프리셋이 늘어도 마이그레이션이 필요 없고,
#: 값은 생성 시 한 번 쓰고 바뀌지 않아서 컬럼 대신 설정에 둔다.
SETTINGS_KEY: Final = "analysis_preset"


def needs_storage(preset: AnalysisPreset) -> bool:
    """이 서버의 NCP 저장소가 있어야 동작하는 스택이면 참."""
    return preset.storage_key == "ncp"


def needs_analysis(preset: AnalysisPreset) -> bool:
    """MI 분석 연결이 있어야 동작하는 스택이면 참. 샷 검출 스택은 분석 서비스를 호출하지 않는다."""
    return preset.adapter_key == registry.MAIU_KEY


def target_required(preset: AnalysisPreset) -> bool:
    """분석 대상(MI 연결·워크스페이스·MI 프로젝트)을 기록해야 영상 등록·분석 요청을 받으면 참.

    MI를 쓰는 스택이고 분석 연동이 켜진 서버일 때 참이다.
    연동이 꺼진 서버에서는 가짜 클라이언트가 응답한다.
    """
    return needs_analysis(preset) and bool(app_settings.maiu_enabled)


def storage_target_required(preset: AnalysisPreset) -> bool:
    """저장소 대상(저장소 연결·버킷)을 기록해야 영상 등록을 받으면 참.

    NCP 저장소를 쓰는 스택이면 분석 연동 스위치(`MAIU_ENABLED`)와 관계없이 참이다.
    저장소 자격증명은 프로젝트가 고른 연결에서만 읽고 서버 기본값이 없다.
    """
    return needs_storage(preset)


def storage_target_missing(preset: AnalysisPreset, project_settings: dict | None) -> bool:
    """저장소 대상을 기록해야 하는데 아직 기록하지 않았으면 참. 화면 안내와 서버 409가 같이 쓴다."""
    target = platform_credentials.storage_target_of(project_settings)
    return storage_target_required(preset) and not (target and target.complete)


def target_missing(preset: AnalysisPreset, project_settings: dict | None) -> bool:
    """분석 대상을 기록해야 하는데 아직 기록하지 않았으면 참. 화면 안내와 서버 409가 같이 쓴다."""
    target = platform_credentials.analysis_target_of(project_settings)
    return target_required(preset) and not (target and target.complete)


def readiness(
    preset: AnalysisPreset, project_settings: dict | None = None,
) -> tuple[bool, bool]:
    """스택의 저장소와 분석을 지금 쓸 수 있는지 돌려준다.

    판정은 등록한 연결을 읽는 `platform_credentials` 하나로 한다.
    프로젝트가 대상을 골랐으면(`project_settings`의 `stack` 블록) 그 연결을 읽을 수 있는지 본다.
    고르지 않았거나 프로젝트를 주지 않았으면(프로젝트 추가 화면의 목록) 고를 수 있는 연결이 하나라도
    등록되어 있는지 본다. 고르지 않은 대상은 `storage_target_missing`·`target_missing`으로 표시한다.
    영상 등록·분석 요청 버튼, 서버의 409가 모두 이 값을 쓴다.

    Returns:
        `(저장소를 쓸 수 있으면 참, 분석을 쓸 수 있으면 참)`. 스택이 쓰지 않는 쪽은 참.
    """
    pc = platform_credentials
    store = pc.storage_target_of(project_settings)
    target = pc.analysis_target_of(project_settings)
    if not needs_storage(preset):
        storage_ok = True
    elif store is not None and store.complete:
        storage_ok = pc.storage_for_or_none(store) is not None
    else:
        storage_ok = pc.any_storage_connection()
    if not needs_analysis(preset) or not app_settings.maiu_enabled:
        analysis_ok = True
    elif target is not None and target.complete:
        analysis_ok = pc.analysis_for_or_none(target) is not None
    else:
        analysis_ok = pc.any_analysis_connection()
    return storage_ok, analysis_ok


def require(
    preset: AnalysisPreset, *, analysis: bool = False, project_settings: dict | None = None,
) -> None:
    """스택의 저장소를 쓸 수 없으면 예외를 던진다. `analysis`가 참이면 분석 연결도 확인한다.

    영상 등록과 분석 요청이 처리를 시작하기 전에 부른다.
    도중에 실패하면 영상 행이나 잡이 일부만 남는다.
    대상을 고르지 않은 경우는 이 함수 대신 `project_stack.gate`가 409 `*_stack_unset`으로 막는다.

    Raises:
        platform_credentials.CredentialsMissingError: 저장소(`storage_not_configured`)나
            분석(`analysis_not_configured`) 연동이 없는 경우. api가 409로 바꾼다.
    """
    pc = platform_credentials
    storage_ok, analysis_ok = readiness(preset, project_settings)
    if not storage_ok:
        raise pc.CredentialsMissingError(pc.STORAGE_NOT_CONFIGURED)
    if analysis and not analysis_ok:
        raise pc.CredentialsMissingError(pc.ANALYSIS_NOT_CONFIGURED)


def _with_readiness(preset: AnalysisPreset) -> AnalysisPreset:
    """정적 카탈로그 값에 등록한 연결의 상태를 더한 사본을 돌려준다.

    아직 구현되지 않은 스택(`available=False`)은 정적 사유를 그대로 둔다.
    등록한 연결이 없는 스택은 빠진 연결을 사유에 적고 `missing_providers`에 서비스를 담는다.
    사유는 프로젝트 추가 화면에 그대로 나간다. 화면은 서비스 목록으로 자격증명 연결 화면을 안내한다.
    """
    if not preset.available:
        return preset
    storage_ok, analysis_ok = readiness(preset)
    if storage_ok and analysis_ok:
        return preset
    providers = [p for p, ok in ((platform_credentials.STORAGE_PROVIDER, storage_ok),
                                 (platform_credentials.ANALYSIS_PROVIDER, analysis_ok)) if not ok]
    if not storage_ok and not analysis_ok:
        missing = "NCP 저장소 연결과 분석 연결이"
    elif not storage_ok:
        missing = "NCP 저장소 연결이"
    else:
        missing = "분석 연결이"
    return preset.model_copy(update={
        "available": False,
        "unavailable_reason": f"등록한 {missing} 없어 선택할 수 없습니다.",
        "missing_providers": providers,
    })


def all_presets() -> list[AnalysisPreset]:
    """카탈로그 전부를 이 서버의 연동 상태와 함께 돌려준다.

    쓸 수 없는 프리셋도 넣는다. 화면이 이유와 함께 표시한다.
    """
    return [_with_readiness(p) for p in _ALL]


def get(key: str) -> AnalysisPreset | None:
    """키로 프리셋을 찾아 이 서버의 연동 상태와 함께 돌려준다. 카탈로그에 없는 키면 `None`이다.

    없는 키를 기본 프리셋으로 바꾸면 운영자가 고른 스택과 실제로 실행되는 스택이 달라진다.
    그 차이는 분석이 끝난 뒤에야 드러난다.
    """
    found = next((p for p in _ALL if p.key == key), None)
    return _with_readiness(found) if found else None


def of_project(settings: dict | None) -> AnalysisPreset:
    """프로젝트의 분석 스택을 돌려준다. 정하지 않았으면 기본값(`DEFAULT_KEY`)이다.

    카탈로그에 없는 키가 저장돼 있으면(프리셋이 코드에서 사라진 경우) `NCP_MI`를 돌려준다.
    주의: 이 대체는 로그를 남기지 않는다.
    """
    key = (settings or {}).get(SETTINGS_KEY) or DEFAULT_KEY
    return get(key) or NCP_MI
