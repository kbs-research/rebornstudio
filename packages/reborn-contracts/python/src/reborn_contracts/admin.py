"""관리 작업 스키마. 최초 설정과 연동 진단을 다룬다."""
from datetime import datetime

from pydantic import BaseModel, Field


class SetupStatus(BaseModel):
    """최초 설정 필요 여부. 인증 없이 볼 수 있는 유일한 설정 정보.

    화면은 로그인 전에 이 값을 보고 로그인 화면과 최초 설정 화면 중 하나로 보낸다.
    그래서 판정 결과와 설정 방법 안내만 담는다.
    사용자 수나 설정값 같은 시스템 내부 정보는 싣지 않는다.
    초기화 전 서버에는 누구나 요청을 보낼 수 있기 때문이다.
    """
    uninitialized: bool
    token_required: bool = Field(
        description="셋업 토큰을 요구하는가. Redis가 없는 로컬 개발에선 false",
    )


class SetupRequest(BaseModel):
    """첫 super_admin 계정 생성 요청."""
    email: str = Field(max_length=255)
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=200)
    token: str = Field(default="", description="서버 로그에 찍힌 셋업 토큰")
    privacy_consent: bool = Field(
        default=False, description="개인정보 수집·이용 동의. 거짓이면 422 privacy_consent_required",
    )


class DiagnosticCheck(BaseModel):
    """연동 진단 결과의 한 항목."""
    key: str
    label: str
    status: str = Field(
        description="ok | warn | fail | unchecked(점검 기록이 없어 확인 전. 주의로 세지 않는다)")
    detail: str = Field(description="무엇을 봤는지 — 실측값")
    hint: str | None = Field(default=None, description="고치는 법")


class DiagnosticsReport(BaseModel):
    """연동 진단 결과. `failed>0`이면 해당 기능이 실제로 동작하지 않는다."""
    checks: list[DiagnosticCheck]
    failed: int
    #: 이 결과를 측정한 시각. `None`이면 아직 한 번도 측정하지 않았다.
    #: 화면이 오래된 결과를 현재 상태로 읽지 않게 한다.
    #: 15분 주기 점검과 사람이 실행한 점검이 모두 이 값을 채운다.
    checked_at: datetime | None = None
