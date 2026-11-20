"""인증 도메인 스키마. 가입·로그인·세션·사용자 정보를 다룬다."""
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, StringConstraints

#: 계정 상태. 계정은 활성으로 생기고, 막을 때는 정지한다.
ApprovalStatus = Literal["active", "suspended"]
#: 플랫폼 역할. 최고관리자는 계정·연결·프로젝트 생성을 맡고, 일반 사용자는 멤버십으로만 일한다.
GlobalRole = Literal["super_admin", "user"]


class EmailLoginRequest(BaseModel):
    """이메일 로그인 요청."""
    email: EmailStr
    password: str = Field(min_length=1)


class PasswordChangeRequest(BaseModel):
    """로그인한 사용자의 비밀번호 변경 요청."""
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=12)


class PasswordResetConfirm(BaseModel):
    """비밀번호 재설정 확정 요청.

    관리자가 준 링크의 토큰과 새 비밀번호를 보낸다.
    """
    token: str
    new_password: str = Field(min_length=12)


class PasswordResetIssued(BaseModel):
    """super_admin이 발급한 1회용 비밀번호 재설정 토큰.

    서버는 토큰 평문을 저장하지 않는다. 그래서 토큰은 이 응답에서만 볼 수 있다.
    화면은 이 사실을 알리고, 다시 보려면 재발급하게 한다.
    `valid_minutes`는 서버가 함께 준다. 화면이 유효 시간을 따로 적어 두지 않게 한다.
    """
    user_id: UUID
    email: EmailStr
    token: str
    expires_at: datetime
    valid_minutes: int


class PasswordResetCheck(BaseModel):
    """재설정 링크의 사용 가능 여부 확인 요청.

    토큰은 주소가 아니라 본문으로 보낸다. 주소에 실으면 접속 기록에 남는다.
    링크 없이 들어온 화면은 빈 토큰으로 유효 시간만 요청한다.
    """
    token: str = Field(default="", max_length=512)


#: 재설정 링크를 쓸 수 없는 이유. `expired` 유효 시간이 지남 · `used` 이미 비밀번호를 바꿈 ·
#: `unknown` 발급 기록에 없음(잘못 복사했거나 재발급으로 바뀐 링크).
PasswordResetReason = Literal["expired", "used", "unknown"]


class PasswordResetStatus(BaseModel):
    """재설정 링크의 상태. 화면이 새 비밀번호를 받기 전에 링크가 살아 있는지 알린다.

    `valid`가 거짓이면 `reason`이 이유를 준다. 토큰은 추측할 수 없는 길이의 난수라서
    이유를 알려도 새 토큰을 찾는 단서가 되지 않는다. 이유를 숨기면 링크를 받은 사람이
    만료인지 이미 썼는지 몰라 최고관리자에게 무엇을 요청할지 정하지 못한다.
    토큰 없이 유효 시간만 요청하면 `reason`은 `None`이다.
    """
    valid: bool
    expires_at: datetime | None = None
    valid_minutes: int
    reason: PasswordResetReason | None = None


class UserDTO(BaseModel):
    """사용자 한 명의 정보."""
    user_id: UUID
    email: EmailStr
    display_name: str
    global_role: GlobalRole
    approval_status: ApprovalStatus
    email_verified: bool
    avatar_url: str | None = None
    # 개인정보(선택 입력)와 수집·이용 동의 시각.
    # 필수 동의 시각이 None이면 동의 화면에서 동의하기 전까지
    # 업무 API가 403 privacy_consent_required로 응답한다.
    # 선택 동의 시각이 None이면 선택 항목(전화번호·생년월일·주소)을 저장하지 않는다.
    phone_number: str | None = None
    birth_date: date | None = None
    address: str | None = None
    privacy_consented_at: datetime | None = None
    optional_privacy_consented_at: datetime | None = None
    #: 계정 생성 시각.
    created_at: datetime | None = None


class SessionDTO(BaseModel):
    """로그인 세션의 access 토큰.

    refresh 토큰은 본문에 싣지 않고 HttpOnly 쿠키(`reborn_refresh`, `Path=/api/auth`)로 준다.
    """
    access_token: str
    access_expires_at: datetime


class LoginResponse(BaseModel):
    """로그인 응답. 정지된 계정은 이 응답 대신 403을 받는다."""
    user: UserDTO
    session: SessionDTO


class UserCreateRequest(BaseModel):
    """super_admin이 계정을 만들 때 보내는 본문.

    공개 가입이 없으므로 계정은 관리자만 만든다. 비밀번호는 받지 않는다.
    만든 계정에는 1회용 재설정 링크를 발급하고, 사용자가 그 링크로 비밀번호를 정한다.
    """
    email: EmailStr
    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    ]
    global_role: GlobalRole = "user"


class UserCreated(BaseModel):
    """계정 생성 결과. 만든 계정과 비밀번호를 정할 1회용 재설정 링크 토큰."""
    user: UserDTO
    reset: PasswordResetIssued


class UserRoleUpdate(BaseModel):
    """super_admin이 사용자의 `global_role`을 바꿀 때 보내는 본문."""
    role: GlobalRole


class UserStatusUpdate(BaseModel):
    """super_admin이 사용자를 active 또는 suspended로 바꿀 때 보내는 본문."""
    status: ApprovalStatus
