"""프로젝트 도메인 스키마."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from reborn_contracts.clip import CLIP_MIN_SECONDS
from reborn_contracts.review import RejectReasonDTO

#: 프로젝트 상태. 운영 중·일시정지·종료 셋이다.
#: DB 제약은 `archived`도 받지만 동작이 `completed`와 같아 서버가 읽을 때 `completed`로 내보낸다.
#: 수정 요청도 이 세 값만 받으므로 `archived`는 422다.
ProjectStatus = Literal["active", "paused", "completed"]
#: 프로젝트 역할. 관리자·검수자·라벨러 셋이다.
ProjectRole = Literal["project_admin", "reviewer", "annotator"]
MemberStatus = Literal["pending", "active", "suspended", "left", "rejected"]


class CaptionQcSettingsDTO(BaseModel):
    """캡션 품질검수 설정. 분석 프리셋과 분리한다.

    MI로 분석하면서 품질검수는 Claude로 하는 조합도 정상적인 구성이기 때문이다.
    값은 `settings.caption_qc`에 저장하고, 서버가 같은 규칙으로 다시 검증한다.
    """

    #: 품질검수를 실행하면 True. 끄면 전혀 실행하지 않는다. 기존 프로젝트의 기본값은 꺼짐이다.
    enabled: bool = False
    #: 카탈로그 키(`dummy`·`llm`·`bedrock`).
    adapter_key: str = "llm"
    #: 자격증명 연결(`connections`)의 이름. id로 지정하면 연결을 다시 만들 때 끊긴다.
    connection_name: str | None = None
    prompts: dict[str, str] = Field(default_factory=dict)
    glossary: dict[str, str] = Field(default_factory=dict)
    #: 비속어 사전. RebornStudio는 비워 두고 화면에서 고치지 않는다.
    profanity: list[str] = Field(default_factory=list)


class CaptionQcInput(BaseModel):
    """캡션 품질검수 설정 변경 요청. 켜기와 검사 방식, 모델 연결만 받는다.

    프롬프트·용어집·비속어 사전은 고정 기본값(비움)이라 받지 않는다.
    모르는 필드는 422로 거절한다. 무시하면 고정값을 보낸 요청이 200을 받고도 아무것도 바뀌지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    #: 카탈로그 키(`dummy`·`llm`·`bedrock`).
    adapter_key: str = "llm"
    #: 자격증명 연결(`connections`)의 이름.
    connection_name: str | None = None


class ProjectCreateRequest(BaseModel):
    """프로젝트 생성 요청. 프로젝트는 기본 조직에 속한다. 처리 옵션은 고정 기본값이라 받지 않는다.

    모르는 필드는 422로 거절한다. 무시하면 옵션을 보낸 요청이 성공하고도 그 옵션이 적용되지 않는다.
    """

    model_config = ConfigDict(extra="forbid")
    #: 주소 식별자. 이 정규식이 기준이고
    #: 화면(`features/projects/pages/project_new.tsx`)도 같은 식을 쓴다.
    #: 3~50자이고 첫 글자는 영문 소문자나 숫자다.
    #: 하이픈으로 시작하면 주소가 명령행 옵션처럼 읽힌다.
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", min_length=3, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    contract_deadline: date | None = None
    target_hours: float | None = Field(default=None, ge=0)
    #: 분석 스택. 생성할 때 고정되고 나중에 바꿀 수 없다. 화면이 이 사실을 알린다.
    #: 주지 않으면 기본 조합(`ncp-mi`)을 쓴다.
    analysis_preset: str | None = None
    caption_qc: CaptionQcInput | None = None


class ProjectUpdateRequest(BaseModel):
    """프로젝트 설정 변경 요청(project_admin).

    보내지 않은 필드는 바꾸지 않는다.
    `null`을 보내면 비울 수 있는 필드(`description`, `guide_md`, `contract_deadline`,
    `target_hours`)는 값을 지우고, 나머지 필드는 422를 돌려준다.
    고정 기본값인 처리 옵션처럼 모르는 필드는 422로 거절한다. 무시하면 200을 받고도 바뀌지 않는다.
    """

    model_config = ConfigDict(extra="forbid")
    caption_qc: CaptionQcInput | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    guide_md: str | None = None  # 작업 가이드(마크다운)
    contract_deadline: date | None = None
    target_hours: float | None = Field(default=None, ge=0)
    project_status: ProjectStatus | None = Field(
        default=None, description="active/paused/completed",
    )
    # 비식별화를 하면 True. 켜면 MI가 raw를 볼 수 없다.
    # 켰는데 비식별화를 할 수 없으면 처리를 멈춘다.
    deident_enabled: bool | None = None


class ProjectDTO(BaseModel):
    """프로젝트 한 건의 정보."""
    project_id: UUID
    slug: str
    name: str
    description: str | None = None
    guide_md: str | None = None  # 작업 가이드(마크다운)
    status: ProjectStatus
    contract_deadline: date | None = None
    target_hours: float | None = None
    target_deident_pct: float
    retain_original: bool
    deident_enabled: bool
    caption_min_tokens: int
    #: 설정 원본. 관리자에게만 그대로 싣고, 그 밖의 역할에게는 공개 키만 싣는다.
    #: 가리는 규칙은 서버 `org.project.redact_for_role` 한 곳에 있다.
    settings: dict

    # ── 분석 스택 ──────────────────────────────────────────────────
    # 값은 `settings["analysis_preset"]`에 저장하고(컬럼을 두지 않는 이유는 `presets.SETTINGS_KEY`)
    # DTO의 최상위 필드로 올린다. 화면이 `settings` dict를 파싱하면 키 이름과 기본값 규칙이 화면에도
    # 생겨, 서버가 규칙을 바꿔도 화면은 이전 규칙을 쓴다.
    # 라벨은 키와 같은 카탈로그에서 나오므로 화면이 카탈로그를 다시 부르지 않고 스택 이름을 쓴다.
    analysis_preset: str
    analysis_preset_label: str
    #: 이 서버에서 그 스택의 저장소를 지금 쓸 수 있으면 참. 거짓이면 영상 등록·재생·납품이 막힌다.
    #: 저장소 대상을 고르지 않았거나 고른 연결을 읽지 못하면 거짓이다.
    #: 화면은 이 값으로 등록 버튼을 끄고 사유를 보여 준다. 판정은 서버의 `presets.readiness` 하나다.
    storage_ready: bool = True
    #: 이 서버에서 그 스택의 분석 요청을 지금 보낼 수 있으면 참. MI를 쓰지 않는 스택은 늘 참이다.
    analysis_ready: bool = True
    #: 분석 대상(MI 연결·워크스페이스·MI 프로젝트)을 기록하지 않아 영상 등록과 분석 요청이
    #: 막혀 있으면 참. 화면은 설정의 분석 연동으로 안내한다. 판정은 `presets.target_missing`이다.
    #: 영상이 있는 프로젝트는 다음 등록이 기본값으로 고정 기록할 수 있으면 거짓이다.
    analysis_stack_missing: bool = False
    #: 저장소 대상(저장소 연결·버킷)을 기록하지 않아 영상 등록이 막혀 있으면 참.
    #: 판정은 `presets.storage_target_missing`이고 잠긴 프로젝트의 규칙도 분석 대상과 같다.
    storage_stack_missing: bool = False
    #: 사람이 넣거나 고치는 구간의 최소 길이(초). 클립 길이 하한, 정하지 않았으면 10초다.
    #: 구간 추가 창과 경계 편집기가 이 값으로 먼저 막는다.
    #: 판정은 서버 `scene_options.clip_min_seconds`다.
    clip_min_seconds: int = CLIP_MIN_SECONDS
    #: 캡션 품질검수 설정. 분석 스택과 같은 이유로 최상위 필드로 올린다.
    #: 화면이 `settings` dict를 파싱하면 키 이름·기본값 규칙이 화면에도 생겨 서버와 달라진다.
    #: 관리자가 아니면 켜짐 여부와 검사 방식만 싣는다.
    #: 연결·프롬프트·용어집·비속어 사전은 비운다.
    caption_qc: CaptionQcSettingsDTO
    #: 반려 사유 목록. 검수 화면은 `enabled`인 사유만 칩으로, 설정 화면은 모두 그린다.
    #: 값은 `settings.reject_reasons`에 저장하고, 정하지 않은 프로젝트는 기본 목록을 쓴다.
    #: 화면이 `settings`를 뒤지지 않게 최상위 필드로 올린다(`caption_qc`와 같은 이유).
    reject_reasons: list[RejectReasonDTO] = Field(default_factory=list)

    my_role: ProjectRole | None = None  # 현재 사용자의 이 프로젝트 역할
    created_at: datetime


class LeaveProjectRequest(BaseModel):
    """작업자의 프로젝트 나가기 요청. 사유는 선택이다."""
    reason: str | None = None


class LeaveProjectResponse(BaseModel):
    """프로젝트 나가기 결과. 보유하던 라벨링·검수 작업 중 대기열로 돌려놓은 수(둘의 합)를 담는다."""
    released: int


class MemberRoleUpdate(BaseModel):
    """멤버 역할 변경 요청."""
    role: ProjectRole


class MemberInviteRequest(BaseModel):
    """project_admin이 기존 사용자를 이메일로 프로젝트에 직접 초대하는 요청."""
    email: EmailStr
    role: ProjectRole = "annotator"


class ProjectMemberDTO(BaseModel):
    """프로젝트 멤버 한 명의 정보."""
    project_id: UUID
    user_id: UUID
    role: ProjectRole
    status: MemberStatus
    joined_at: datetime
    approved_at: datetime | None = None
    # 사용자 정보. list_members에서만 채운다.
    email: str | None = None
    display_name: str | None = None
    #: 필수 개인정보 동의 시각. 관리자에게만 싣는다.
    privacy_consented_at: datetime | None = None


class AnalysisPresetDTO(BaseModel):
    """분석 스택 하나. 프로젝트 생성 화면의 선택 목록에 쓴다.

    쓸 수 없는 스택도 목록에 넣는다.
    빼면 운영자가 그 스택을 쓸 수 없다는 것을 알 수 없어 계속 찾는다.
    없는 것과 아직 쓸 수 없는 것은 다르다.
    그래서 `available=False`면 `unavailable_reason`이 반드시 있다.
    """

    key: str
    #: 고르지 않았을 때 쓰는 스택이면 True. 화면이 하드코딩하지 않게 서버가 알려 준다.
    #: 서버 기본값이 바뀌어도 폼이 이전 기본값을 쓰지 않게 한다.
    is_default: bool = False
    label: str
    description: str = ""
    available: bool = True
    unavailable_reason: str | None = None
    #: 등록한 연결이 없어 쓸 수 없을 때 먼저 등록해야 하는 연결의 서비스(`ncp`·`maiu`).
    #: 화면이 자격증명 연결 화면으로 가는 경로를 함께 보인다.
    missing_providers: list[str] = Field(default_factory=list)
    #: 원본을 저장하는 곳. 분석기와 짝이 맞아야 하는 두 번째 요소다.
    storage_key: str = "ncp"
    #: 캡션 초안을 만드는 어댑터. 화면이 스택 구성을 표시할 때 쓴다.
    #: 벤더 비용·호출 화면을 보여 줄지 판단하는 근거이기도 하다.
    #: 벤더 분석기가 없는 스택(`ncp-shots`)에 벤더 비용 카드를 띄우면
    #: 쓰지 않는 비용을 쓰는 것처럼 보인다.
    #: 화면이 프리셋 키를 하드코딩해 짐작하지 않게 서버가 준다.
    adapter_key: str = ""
    #: 씬을 자르는 어댑터. `None`이면 분석기가 함께 자른다.
    #: 그때 화면은 "씬 분할: 벤더 위임"으로 표시한다.
    scene_adapter_key: str | None = None


class ProjectAnalysisStackDTO(BaseModel):
    """프로젝트가 고른 MI 연결·워크스페이스·MI 프로젝트(`settings.stack`).

    세 필드를 함께 고른다. 세 필드가 모두 비면 아직 기록하지 않은 상태이고, 채워 줄 기본값은 없다.
    `PUT /projects/{id}/analysis-stack`의 요청과 응답이 같은 모양이다.
    """

    #: 등록한 `maiu` 연결 id.
    analysis_connection_id: UUID | None = None
    maiu_workspace: str | None = Field(default=None, max_length=63)
    maiu_project_id: str | None = Field(default=None, max_length=64)


class AnalysisConnectionOption(BaseModel):
    """MI 연결 선택지 하나. 고르는 데 필요한 이름과 점검 상태만 싣는다."""

    id: UUID
    name: str
    has_secret: bool = False
    #: 마지막 점검 결과(`ok`·`warn`·`fail`). 점검 전이면 `None`이다.
    last_status: str | None = None
    #: 고를 수 있으면 참. 자격증명이 있고 마지막 점검이 정상인 연결만 고른다.
    selectable: bool = False


class MaiuWorkspaceOption(BaseModel):
    """MI 워크스페이스 선택지 하나."""

    name: str
    #: 워크스페이스에 연동된 저장소 이름. 목록 응답에 없으면 `None`이다.
    bucket: str | None = None


class MaiuProjectOption(BaseModel):
    """MI 프로젝트 선택지 하나."""

    project_id: str
    name: str
    #: MI가 워크스페이스마다 만드는 기본 프로젝트면 참.
    is_base_project: bool = False
    #: 설정의 옛 `maiu_project_id`가 가리키는 이 프로젝트용 MI 프로젝트면 참.
    provisioned: bool = False


class AnalysisChoicesDTO(BaseModel):
    """프로젝트 설정의 분석 대상 화면이 쓰는 현재 선택·잠금·선택지.

    `GET /projects/{id}/analysis-choices`가 준다. `connection_id`·`workspace` 쿼리로 고른
    연결의 워크스페이스 목록과, 고른 워크스페이스의 연동 저장소·MI 프로젝트 목록을 함께 싣는다.
    외부 조회가 실패하면 500 대신 `error`에 사유 문장을 싣고 해당 목록을 `None`으로 둔다.
    """

    #: 저장된 선택. 세 필드가 모두 비면 아직 기록하지 않은 상태다.
    selected: ProjectAnalysisStackDTO
    #: 프로젝트의 분석 방식이 MI를 쓰면 참. 거짓이면 이 선택을 저장할 수 없다.
    supported: bool = True
    #: 선택을 기록해야 영상 등록과 분석 요청을 받으면 참(MI 방식이고 분석 연동이 켜진 서버).
    required: bool = False
    #: 영상이 하나라도 있어 선택을 바꿀 수 없으면 참.
    locked: bool = False
    #: 이 프로젝트의 영상 수. 삭제한 영상과 올리는 중인 영상도 센다. 잠금의 근거다.
    video_count: int = 0
    #: `video_count` 중 삭제한 영상 수. 영상 목록이 세지 않는 영상이라 화면이 분모로 밝힌다.
    deleted_video_count: int = 0
    #: 프로비저닝이 설정 최상위에 남긴 MI 프로젝트 id. 분석 요청은 이 값을 쓰지 않는다.
    legacy_maiu_project_id: str | None = None
    #: 저장된 MI 프로젝트의 이름. 저장할 때나 목록을 읽을 때 확인한 이름이다.
    #: MI 목록을 못 읽는 동안에도 화면이 `이름 · id N`으로 보이게 둔다.
    #: 확인한 적이 없으면 `None`이다.
    selected_project_name: str | None = None
    #: 이 프로젝트가 고른 저장소 이름. 워크스페이스 연동 저장소가 이 값과 같아야 고를 수 있다.
    #: 저장소 대상을 저장하지 않았으면 `None`이다.
    project_bucket: str | None = None
    #: 등록한 MI 연결. 비어 있으면 자격증명 연결에서 먼저 등록해야 한다.
    connections: list[AnalysisConnectionOption] = Field(default_factory=list)
    #: 목록을 읽은 연결 id. `None`이면 목록을 읽지 않았다.
    browse_connection_id: UUID | None = None
    #: MI 프로젝트 목록을 읽은 워크스페이스.
    browse_workspace: str | None = None
    #: 고른 연결의 워크스페이스. 읽지 않았거나 읽지 못했으면 `None`이다.
    workspaces: list[MaiuWorkspaceOption] | None = None
    #: 고른 워크스페이스의 연동 저장소. 읽지 않았거나 읽지 못했으면 `None`이다.
    workspace_bucket: str | None = None
    #: 고른 워크스페이스의 MI 프로젝트. 읽지 않았거나 읽지 못했으면 `None`이다.
    projects: list[MaiuProjectOption] | None = None
    #: 외부 조회 실패 사유. 화면에 그대로 나가는 문장이다.
    error: str | None = None
    #: `error`의 실패가 모두 다시 읽으면 풀릴 일시 오류(요청량 제한·서버 오류·응답 지연)면 참.
    #: 연동 점검의 주의(`warn`)와 같은 분류다.
    error_transient: bool = False


class ProjectStorageStackDTO(BaseModel):
    """프로젝트가 고른 저장소 연결·버킷(`settings.stack`).

    두 필드를 함께 고른다. 두 필드가 모두 비면 아직 기록하지 않은 상태이고, 채워 줄 기본값은 없다.
    `PUT /projects/{id}/storage-stack`의 요청과 응답이 같은 모양이다.
    """

    #: 등록한 `ncp` 연결 id.
    storage_connection_id: UUID | None = None
    bucket: str | None = Field(default=None, max_length=63)


class StorageConnectionOption(BaseModel):
    """저장소 연결 선택지 하나. 고르는 데 필요한 이름과 점검 상태만 싣는다."""

    id: UUID
    name: str
    has_secret: bool = False
    #: 마지막 점검 결과(`ok`·`warn`·`fail`). 점검 전이면 `None`이다.
    last_status: str | None = None
    #: 고를 수 있으면 참. 자격증명이 있고 마지막 점검이 정상인 연결만 고른다.
    selectable: bool = False
    #: 연결 설정의 버킷. 연결을 고르면 화면이 버킷 입력란에 먼저 보여 준다.
    bucket: str | None = None
    #: 이 연결로 고를 수 있는 버킷. 연결 설정의 버킷과 허용 버킷 목록의 합이다.
    #: 버킷 목록을 읽지 못해도 화면은 이 안에서 고르게 한다. 비면 고를 수 있는 버킷이 없다.
    allowed_buckets: list[str] = Field(default_factory=list)


class StorageChoicesDTO(BaseModel):
    """프로젝트 설정의 저장소 대상 화면이 쓰는 현재 선택·잠금·선택지.

    `GET /projects/{id}/storage-choices`가 준다. `connection_id` 쿼리로 고른 연결의 버킷 목록을
    싣는다.
    버킷 목록을 읽지 못하면 500 대신 `error`에 사유 문장을 싣고 `buckets`를 `None`으로 둔다.
    """

    #: 저장된 선택. 두 필드가 모두 비면 아직 기록하지 않은 상태다.
    selected: ProjectStorageStackDTO
    #: 프로젝트의 분석 방식이 NCP 저장소를 쓰면 참. 거짓이면 이 선택을 저장할 수 없다.
    supported: bool = True
    #: 선택을 기록해야 영상 등록을 받으면 참(NCP 저장소 방식).
    required: bool = False
    #: 영상이 하나라도 있어 선택을 바꿀 수 없으면 참.
    locked: bool = False
    #: 이 프로젝트의 영상 수. 삭제한 영상과 올리는 중인 영상도 센다. 잠금의 근거다.
    video_count: int = 0
    #: `video_count` 중 삭제한 영상 수. 영상 목록이 세지 않는 영상이라 화면이 분모로 밝힌다.
    deleted_video_count: int = 0
    #: 등록한 NCP 연결. 비어 있으면 자격증명 연결에서 먼저 등록해야 한다.
    connections: list[StorageConnectionOption] = Field(default_factory=list)
    #: 버킷 목록을 읽은 연결 id. `None`이면 목록을 읽지 않았다.
    browse_connection_id: UUID | None = None
    #: 고른 연결로 읽은 버킷 이름. 읽지 않았거나 읽지 못했으면 `None`이다.
    buckets: list[str] | None = None
    #: 버킷 목록을 읽지 못한 사유. 화면에 그대로 나가는 문장이다.
    error: str | None = None
