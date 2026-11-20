"""사용자 프로필 수정, 개인정보 필수 동의 기록과 선택 동의 철회, super_admin용 사용자 조회."""
from datetime import UTC, datetime

from fastapi import HTTPException, status
from reborn_contracts import UserDTO, UserProfileUpdate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import User
from api_gateway.services.auth import consent
from api_gateway.services.auth.service import _to_dto

#: `approval_status`가 가질 수 있는 값.
_VALID_STATUS = {"active", "suspended"}


async def update_me(db: AsyncSession, user: User, payload: UserProfileUpdate) -> UserDTO:
    """본인 프로필을 부분 수정한다(display_name, avatar_url).

    이 경로에 오는 사람은 이미 필수 동의를 했다(`dependencies.get_current_user`).
    선택 개인정보(전화번호·생년월일·주소)는 받지 않는다. 이미 저장된 값은 그대로 두고,
    선택 동의 철회(`withdraw_optional_me`)가 지운다.

    필수 동의 철회(`privacy_consent=False`)는 받지 않는다. 필수 동의는 계정을 쓰는 조건이라
    철회는 계정 삭제 요청으로 처리한다(`consent.reject_withdrawal`).

    Raises:
        HTTPException: 필수 동의 철회를 요청한 경우(422 `privacy_consent_withdrawal_unavailable`).
    """
    if payload.privacy_consent is False:
        consent.reject_withdrawal()
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url

    await db.commit()
    await db.refresh(user)
    return _to_dto(user)


async def consent_me(db: AsyncSession, user: User) -> UserDTO:
    """로그인한 사용자의 필수 동의를 기록한다.

    동의 화면이 부른다. 이미 동의했으면 바꾸지 않는다.
    같은 요청이 두 번 와도 동의 시각과 감사 이벤트는 한 번만 남는다.
    """
    changed = False
    if consent.is_missing(user):
        user.privacy_consented_at = datetime.now(UTC)
        consent.record(db, user.user_id, via="consent_screen")
        changed = True
    if changed:
        await db.commit()
        await db.refresh(user)
    return _to_dto(user)


async def withdraw_optional_me(db: AsyncSession, user: User) -> UserDTO:
    """선택 동의를 철회하고 선택 항목을 바로 지운다. 동의가 없으면 아무것도 바꾸지 않는다."""
    if consent.withdraw_optional(db, user):
        await db.commit()
        await db.refresh(user)
    return _to_dto(user)


async def list_all(db: AsyncSession, status_filter: str | None = None) -> list[UserDTO]:
    """모든 사용자를 최신순으로 돌려준다. `status_filter`로 `approval_status`를 거를 수 있다.

    Raises:
        HTTPException: `status_filter`가 알 수 없는 값인 경우(422 `invalid_status`).
    """
    stmt = select(User).order_by(User.created_at.desc())
    if status_filter:
        if status_filter not in _VALID_STATUS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_status")
        stmt = stmt.where(User.approval_status == status_filter)
    return [_to_dto(u) for u in await db.scalars(stmt)]
