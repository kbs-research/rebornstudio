"""내 프로필 수정·개인정보 동의와 super_admin용 사용자 목록을 다루는 사용자 API 라우터."""
from fastapi import APIRouter, Depends, Query
from reborn_contracts import UserDTO, UserProfileUpdate
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import (
    get_current_user,
    get_db,
    get_signed_in_user,
    require_super_admin,
)
from api_gateway.models import User
from api_gateway.services.auth import user as user_service

router = APIRouter()


@router.patch("/me", response_model=UserDTO, summary="내 프로필 수정")
async def update_me(
    payload: UserProfileUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """현재 인증된 사용자의 프로필(표시 이름 등)을 수정한다.

    필수 동의 철회(`privacy_consent=false`)는
    422 `privacy_consent_withdrawal_unavailable`로 거절한다.
    """
    return await user_service.update_me(db, user, payload)


@router.post("/me/privacy-consent", response_model=UserDTO, summary="개인정보 수집·이용 동의")
async def consent_me(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_signed_in_user),
):
    """현재 사용자의 필수 동의를 기록한다.

    동의하지 않은 사람도 부를 수 있는 경로다.
    업무 API는 필수 동의 전까지 403 `privacy_consent_required`를 돌려준다.
    이미 동의한 항목은 바꾸지 않고 현재 정보를 돌려준다.
    """
    return await user_service.consent_me(db, user)


@router.delete(
    "/me/optional-privacy-consent", response_model=UserDTO, summary="선택 항목 수집·이용 동의 철회",
)
async def withdraw_optional_me(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """현재 사용자의 선택 동의를 철회하고 선택 항목을 바로 지운다. 동의가 없으면 바꾸지 않는다.

    선택 동의를 새로 받는 경로는 없다. 선택 동의 기록이 있는 계정이 철회할 수 있게 철회 경로만 둔다.
    """
    return await user_service.withdraw_optional_me(db, user)


@router.get("", response_model=list[UserDTO], summary="전체 사용자 목록 (super_admin)")
async def list_users(
    status_filter: str | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    """전체 사용자 목록을 돌려준다. super_admin만 부를 수 있다.

    `status` 쿼리로 계정 상태(활성·정지)를 거를 수 있다.
    """
    return await user_service.list_all(db, status_filter)
