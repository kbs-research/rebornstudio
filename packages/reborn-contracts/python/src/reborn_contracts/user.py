"""사용자 프로필 수정 스키마."""
from pydantic import BaseModel, Field


class UserProfileUpdate(BaseModel):
    """로그인한 사용자의 프로필 수정 요청."""
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=500)
    privacy_consent: bool | None = Field(
        default=None,
        description=(
            "거짓이면 422 privacy_consent_withdrawal_unavailable. "
            "동의는 필수라서 철회는 계정 삭제 요청으로 처리한다. "
            "동의 기록은 POST /users/me/privacy-consent"
        ),
    )
