"""개인정보 수집·이용 동의의 판정과 기록.

동의는 필수와 선택으로 나뉜다.
판정 규칙은 이 모듈에만 둔다. 최초 설정과 요청 가드가 각자 판정하면 한쪽만 바뀐다.

필수 동의(`privacy_consented_at`)는 이메일·표시 이름·비밀번호와 자동 수집 기록을 덮는다.
최초 설정은 동의가 없으면 422로 거절한다.
최고관리자가 만든 계정은 동의 없이 생기므로, 첫 로그인 뒤 동의하기 전까지 업무 API를 쓰지 못한다
(`dependencies.get_current_user`).
필수 동의를 거두면 계정을 쓸 수 없으므로 철회는 계정 삭제 요청으로 처리한다.

선택 동의(`optional_privacy_consented_at`)는 전화번호·생년월일·주소를 덮는다.
새 선택 동의와 선택 항목 입력은 받지 않는다. 이미 동의하고 값을 남긴 계정은 언제든 철회할 수 있고,
철회하면 선택 항목을 바로 지운다.
동의를 거둔 뒤에도 값이 남으면 동의 없이 보유하는 상태가 되기 때문이다.
"""
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import User
from api_gateway.services import audit

#: 선택 동의가 덮는 필드. 철회 때 이 목록을 지운다.
OPTIONAL_FIELDS = ("phone_number", "birth_date", "address")


def is_missing(user: User) -> bool:
    """동의 기록이 없는 계정이면 True."""
    return user.privacy_consented_at is None


def require_present(user: User) -> None:
    """동의한 계정만 통과시킨다. 업무 API의 요청 가드가 부른다.

    Raises:
        HTTPException: 동의 기록이 없는 계정인 경우(403 `privacy_consent_required`).
    """
    if is_missing(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "privacy_consent_required")


def require_given(consent: bool) -> None:
    """계정을 만드는 요청에 동의가 실렸는지 확인한다.

    Raises:
        HTTPException: 동의가 없는 경우(422 `privacy_consent_required`).
    """
    if not consent:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "privacy_consent_required")


def reject_withdrawal() -> None:
    """필수 동의 철회 요청을 거절한다.

    Raises:
        HTTPException: 항상(422 `privacy_consent_withdrawal_unavailable`).
    """
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY, "privacy_consent_withdrawal_unavailable",
    )


def record(db: AsyncSession, user_id: UUID, *, via: str) -> None:
    """필수 동의를 감사 이벤트(`user.privacy_consented`)로 남긴다. 커밋은 호출자가 한다.

    `via`는 동의를 받은 경로다(`setup`·`consent_screen`).
    동의 시각 필드는 다시 동의하면 덮어쓰이므로 동의 이력은 이 이벤트로 남긴다.
    """
    audit.emit(
        db, event_type="user.privacy_consented", target_kind="user",
        target_id=user_id, actor_user_id=user_id, payload={"via": via},
    )



def withdraw_optional(db: AsyncSession, user: User) -> bool:
    """선택 동의를 철회하고 선택 항목을 지운다. 동의가 없으면 아무것도 바꾸지 않는다.

    커밋은 호출자가 한다.

    감사 기록에는 지운 필드 이름만 남긴다. 값을 적으면 그 기록이 개인정보 사본이 되어
    감사 보존 기간 동안 남는다.

    Returns:
        bool: 철회했으면 True.
    """
    if user.optional_privacy_consented_at is None:
        return False
    purged = [f for f in OPTIONAL_FIELDS if getattr(user, f) is not None]
    for f in OPTIONAL_FIELDS:
        setattr(user, f, None)
    user.optional_privacy_consented_at = None
    audit.emit(
        db, event_type="user.optional_privacy_consent_withdrawn", target_kind="user",
        target_id=user.user_id, actor_user_id=user.user_id, payload={"purged": purged},
    )
    return True
