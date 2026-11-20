"""연결(Connection) 계약. 운영자는 자격증명 같은 값만 채운다.

프리셋(스택 조합)은 코드가 정의한다.
그 조합에 필요한 값(엔드포인트·버킷·자격증명)은 운영자가 채운다.
그 값을 담는 곳이 Connection이다.
같은 클라우드 계정의 자격증명은 여러 프로젝트가 함께 쓰므로 프로젝트 밖에 둔다.

시크릿은 이 계약의 응답에 싣지 않는다. `ConnectionDTO`에는 access/secret 키가 없다.
화면이 시크릿을 돌려받으면 그 자체로 유출 경로가 된다.
대신 설정 여부(`has_secret`)와 설정 시각(`secret_set_at`)만 준다.
시크릿은 요청(`*Request`)으로만 넣고, 넣은 뒤에는 읽을 수 없다(쓰기 전용).
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

#: 연결 대상. 저장소·분석기 백엔드 키와 맞춘다(`storage_backend`·`analysis.registry`).
ConnectionProvider = Literal[
    "s3", "ncp", "gcs", "maiu",
    # 캡션 품질검수 모델. 키를 `.env`에 새로 만들지 않는다.
    # `.env`에 두면 프로젝트마다 다른 계정을 쓸 수 없고, 키를 바꿀 때마다 배포해야 한다.
    "openai", "anthropic", "local-llm", "bedrock",
]

#: 새로 만들 수 있는 연결 대상. 저장소는 NCP 하나라 `s3`·`gcs`는 새로 받지 않는다.
#: 이미 저장된 `s3`·`gcs` 행은 `ConnectionProvider`로 그대로 읽고 점검한다.
NewConnectionProvider = Literal[
    "ncp", "maiu", "openai", "anthropic", "local-llm", "bedrock",
]

#: 프로바이더별로 필요한, 비밀이 아닌 설정값의 이름. 화면은 이 목록으로 폼 필드를 그린다.
#: 시크릿(access/secret 키)은 여기 없다. 별도의 쓰기 전용 필드로 받는다.
PROVIDER_FIELDS: dict[str, tuple[str, ...]] = {
    "s3": ("endpoint", "bucket", "region"),
    # `allowed_buckets`는 프로젝트 저장소로 더 허용하는 버킷 이름 목록이다.
    # 연결의 `bucket`은 늘 허용한다.
    "ncp": ("endpoint", "bucket", "region", "allowed_buckets"),
    "gcs": ("bucket",),
    # 분석 프로젝트는 프로젝트마다 분석 대상에서 고른다. 연결에는 키와 점검할 워크스페이스만 둔다.
    "maiu": ("base_url", "workspace_name"),
    # LLM 공급자. `model`은 비밀이 아니라 설정이다.
    # 어느 모델을 쓰는지는 감사 대상이므로 오히려 보여야 한다.
    # `base_url`은 공식 엔드포인트를 쓰면 비워 둔다.
    "openai": ("model", "base_url"),
    "anthropic": ("model",),
    # 로컬·사설 모델(vLLM·Ollama·TGI). OpenAI 호환 엔드포인트 하나로 셋을 지원한다.
    # 공급자마다 어댑터를 만들면 그 수만큼 계약 테스트가 늘어난다.
    "local-llm": ("base_url", "model"),
    # Bedrock. 인증이 SigV4라서 S3처럼 access/secret 두 값을 쓴다.
    # `region`은 비밀이 아니라 설정이다. 리전에 따라 쓸 수 있는 모델이 다르다.
    # 같은 모델이 서울 리전에는 없을 수 있다.
    "bedrock": ("region", "model"),
}


class ConnectionDTO(BaseModel):
    """연결 하나. 시크릿은 값이 아니라 설정 여부로만 보여 준다.

    `config`는 비밀이 아닌 값(엔드포인트·버킷 등)이라 그대로 보여 준다.
    `has_secret`이 `False`면 아직 자격증명을 넣지 않은 상태다.
    화면은 이 연결을 "미완"으로 표시한다.
    연결은 등록됐는데 자격증명이 없으면 오류 표시 없이 인증에 실패하기 때문이다.
    """

    id: UUID
    name: str
    provider: ConnectionProvider
    #: 비밀이 아닌 설정값(엔드포인트·버킷·region·workspace 등). 프로바이더마다 키가 다르다.
    config: dict = Field(default_factory=dict)
    #: 시크릿이 저장돼 있으면 True. 시크릿 값은 절대 주지 않는다.
    has_secret: bool = False
    secret_set_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by: UUID | None = None
    #: 마지막 점검 시각. `None`은 정상이 아니라 아직 점검하지 않았다는 뜻이다.
    #: 연결을 저장할 때 도는 점검과 15분 주기 점검(`check_connections`)이 함께 채운다.
    #: 언제부터 실패했는지는 `domain_events`(`connection.health_changed`)로 알 수 있다.
    #: 이 이벤트는 상태가 바뀐 순간에만 남는다.
    last_checked_at: datetime | None = None
    last_status: str | None = None
    last_detail: str | None = None
    #: 이 연결을 실제로 쓰는 프로젝트 이름 목록(캡션 품질검수의 모델 연결과 분석 대상 연결).
    #: 목록 조회에서만 채운다. 연결을 지우기 전에 무엇이 멈추는지 알려 주려고 둔다.
    #: 비어 있으면 아직 어디에서도 쓰지 않는다는 뜻이다.
    used_by: list[str] = Field(default_factory=list)


class ConnectionChoiceDTO(BaseModel):
    """프로젝트 설정에서 고를 수 있는 연결 하나. 이름과 고르는 데 필요한 상태만 싣는다.

    프로젝트 관리자가 캡션 품질검수의 모델 연결을 고를 때 쓴다
    (`/projects/{id}/connection-choices`). 화면은 `provider`로 고를 목록을 나눈다.
    연결 관리 목록(`ConnectionDTO`)은 설정값·점검 사유·쓰는 프로젝트까지 실어 최고관리자에게만 준다.
    이 목록은 설정값과 시크릿 없이 준다.
    """

    name: str
    provider: ConnectionProvider
    #: 자격증명이 들어 있으면 True. False면 골라도 모델을 부를 수 없다.
    has_secret: bool = False
    #: 마지막 점검 결과(`ok`·`warn`·`fail`). 점검 전이면 `None`이다.
    last_status: str | None = None
    #: 이 연결로 실행할 수 있는 캡션 품질검수 방식(어댑터 키). 비어 있으면 품질검수에 쓸 수 없다.
    #: 화면은 고른 검사 방식이 이 목록에 있는 연결만 보여 준다. 판정은 서버 어댑터 카탈로그에 있다.
    adapter_keys: list[str] = Field(default_factory=list)


#: 연결 선택지를 고르는 목적. `caption_qc`는 품질검수 모델 연결이다.
ConnectionPurpose = Literal["caption_qc"]


class ConnectionProblem(BaseModel):
    """개요가 이름으로 보여 주는 문제 연결 하나.

    쓰는 곳이 있고 마지막 점검이 실패·주의인 연결만 싣는다.
    수만 주면 개요는 어느 연결이 왜 멈췄는지 보여 주지 못한다.
    """

    id: UUID
    name: str
    provider: ConnectionProvider
    #: `fail` 또는 `warn`.
    status: str
    detail: str | None = None
    checked_at: datetime | None = None
    #: 이 연결을 쓰는 프로젝트 이름. 비면 문제 목록에 오지 않는다.
    used_by: list[str] = Field(default_factory=list)


class ConnectionHealthSummary(BaseModel):
    """자격증명 연결 전체의 마지막 점검 집계. 플랫폼 개요가 쓴다.

    플랫폼 연동 점검(`DiagnosticsReport`)은 `.env` 자격증명만 본다.
    등록한 연결은 따로 점검하므로, 두 분모를 따로 보여 주려고 이 값을 둔다.
    이 값이 없으면 등록한 연결이 실패해도 개요는 모두 정상으로 표시한다.

    상태 넷(`ok`·`warn`·`fail`·`unchecked`)은 쓰는 기능이 있는 연결만 센다.
    쓰는 곳이 없는 연결은 상태와 관계없이 `unused`로만 센다.
    그래서 `total`은 다섯 값의 합이다.
    아무 기능도 쓰지 않는 시험 연결의 점검 실패는 개요에 실패로 뜨지 않는다.
    """

    #: 전체 연결 수. 쓰는 곳이 없는 연결도 포함한다.
    total: int = 0
    ok: int = 0
    warn: int = 0
    fail: int = 0
    #: 한 번도 점검하지 않은 연결 수. 정상이 아니라 아직 점검 전이라는 뜻이다.
    unchecked: int = 0
    #: 쓰는 기능이 없는 연결 수. 연결을 읽는 기능은 캡션 품질검수와 비식별화다.
    unused: int = 0
    #: 쓰는 곳이 있는 실패·주의 연결. 실패를 먼저, 같은 상태는 이름 순이다.
    problems: list[ConnectionProblem] = Field(default_factory=list)


class ConnectionCreateRequest(BaseModel):
    """연결 생성 요청. 이름·프로바이더·설정값을 받고 시크릿은 선택으로 받는다.

    시크릿 없이도 만들 수 있다. 값을 나중에 채우는 흐름이다.
    그때 연결은 `has_secret=False`로 보이고, 화면은 "자격증명 필요"로 표시한다.
    """

    name: str = Field(min_length=1, max_length=100)
    provider: NewConnectionProvider
    config: dict = Field(default_factory=dict)
    #: 쓰기 전용이고 응답에 돌아오지 않는다.
    access_key: str | None = Field(default=None, max_length=512)
    secret_key: str | None = Field(default=None, max_length=2048)


class ConnectionUpdateRequest(BaseModel):
    """연결 수정 요청. `None`인 필드는 바꾸지 않고, 보낸 시크릿으로 교체한다.

    시크릿을 보내지 않으면 기존 값을 그대로 둔다. 수정할 때마다 다시 넣게 하지 않는다.
    다만 요청이 나가는 곳(주소·엔드포인트·리전)을 바꾸는 수정은 시크릿을 같은 요청에 다시 받는다.
    저장된 시크릿이 새 주소로 나가지 않게 하려는 규칙이다.
    시크릿을 지우려면 `clear_secret=True`를 명시한다. 빈 문자열과 구분하기 위해서다.
    """

    #: 이름은 바꿀 수 없다. 지금 이름과 다르면 422 `connection_name_immutable`이다.
    #: 프로젝트 설정이 연결을 이름으로 가리켜 바꾸면 그 설정이 끊긴다.
    name: str | None = Field(default=None, min_length=1, max_length=100)
    config: dict | None = None
    access_key: str | None = Field(default=None, max_length=512)
    secret_key: str | None = Field(default=None, max_length=2048)
    #: 시크릿을 비우려면 True. `access_key=None`(바꾸지 않음)과 구분하는 명시 신호다.
    clear_secret: bool = False


class ConnectionTestResult(BaseModel):
    """연결에 실제로 요청을 보내 본 결과. 연동 진단과 같은 `ok|warn|fail` 값을 쓴다.

    저장만 하고 써 보지 않으면 등록은 되는데 분석만 안 되는 실패를 잡을 수 없다.
    그래서 연결을 저장할 때와 15분 주기 점검(`check_connections`)에서 실제 연결을 시험한다.
    """

    status: Literal["ok", "warn", "fail"]
    detail: str
    hint: str | None = None


class ConnectionSaved(ConnectionDTO):
    """연결 생성·수정 응답. 저장한 연결과 저장 직후 점검 결과를 함께 싣는다.

    점검이 실패해도 저장은 그대로 남고, 연결 행의 마지막 점검(`last_status`)이 실패로 기록된다.
    프로젝트는 마지막 점검이 정상인 연결만 고를 수 있으므로 실패한 연결은 고칠 때까지 고를 수 없다.
    """

    #: 저장 직후 점검 결과. `hint`(고치는 방법)는 행에 남지 않아 이 응답에만 있다.
    check: ConnectionTestResult

