"""최고관리자(super_admin) 작업.

계정 생성, 역할·상태 변경, 비밀번호 재설정 링크 발급을 한다.
공개 가입이 없어 가입 승인도 없다. 계정은 만들 때부터 활성이고, 막을 때는 정지한다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import (
    PasswordResetIssued,
    UserCreated,
    UserCreateRequest,
    UserDTO,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import PasswordResetToken, User
from api_gateway.security import hash_password_reset_secret, new_password_reset_secret
from api_gateway.services import audit
from api_gateway.services.auth.service import (
    RESET_LINK_VALID_MINUTES,
    _to_dto,
)
from api_gateway.services.work import service as work_service

#: 관리자가 줄 수 있는 `global_role` 값.
_VALID_ROLES = {"user", "super_admin"}
#: 관리자가 바꿀 수 있는 `approval_status` 값.
_VALID_STATUSES = {"active", "suspended"}



async def set_role(
    db: AsyncSession, target_id: UUID, admin: User, new_role: str
) -> UserDTO:
    """지정한 사용자의 `global_role`을 바꾼다.

    Raises:
        HTTPException: 사용자가 없는 경우(404), 역할이 잘못된 경우(422 `invalid_role`),
            자기 자신의 역할을 바꾸려는 경우(403 `cannot_change_own_role`).
    """
    user = await db.get(User, target_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")
    if new_role not in _VALID_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_role")
    if target_id == admin.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot_change_own_role")
    old_role = user.global_role
    user.global_role = new_role
    audit.emit(
        db, event_type="user.role_changed", target_kind="user", target_id=user.user_id,
        actor_user_id=admin.user_id,
        payload={"from": old_role, "to": new_role},
    )
    await db.commit()
    await db.refresh(user)
    return _to_dto(user)


async def set_status(
    db: AsyncSession, target_id: UUID, admin: User, new_status: str
) -> UserDTO:
    """지정한 사용자를 active 또는 suspended로 바꾼다.

    정지하면 그 사람이 모든 프로젝트에서 보유한 라벨링·검수 작업을 대기열로 돌린다
    (`work.service.release_holds`). 정지된 사람은 로그인할 수 없어 제출도 판정도 하지 못한다.
    보유에는 끝나는 시각도 자동 회수도 없으므로 풀지 않으면 그 작업은 아무도 진행할 수 없다.
    정지를 풀어도 보유는 되돌리지 않는다. 그사이 다른 사람이 받았을 수 있기 때문이다.
    푼 작업의 ID와 수는 `user.status_changed` 감사 기록에 남긴다.

    Raises:
        HTTPException: 사용자가 없는 경우(404), 상태가 잘못된 경우(422 `invalid_status`),
            자기 자신을 정지하려는 경우(403 `cannot_suspend_self`).
    """
    user = await db.get(User, target_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")
    if new_status not in _VALID_STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_status")
    if new_status == "suspended" and target_id == admin.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot_suspend_self")
    old_status = user.approval_status
    user.approval_status = new_status
    payload: dict = {"from": old_status, "to": new_status}
    if new_status == "suspended":
        released = await work_service.release_holds(db, user.user_id)
        payload.update(work_service.released_payload(released))
    audit.emit(
        db, event_type="user.status_changed", target_kind="user", target_id=user.user_id,
        actor_user_id=admin.user_id, payload=payload,
    )
    await db.commit()
    await db.refresh(user)
    return _to_dto(user)


async def create_user(
    db: AsyncSession, payload: UserCreateRequest, admin: User
) -> UserCreated:
    """최고관리자가 활성 계정을 만들고 비밀번호를 정할 1회용 재설정 링크를 발급한다.

    공개 가입이 없으므로 계정은 이 경로로만 생긴다. 비밀번호는 받지 않고 비워 둔다.
    사용자는 관리자가 전달한 링크로 비밀번호를 정하고, 첫 로그인 때 개인정보 수집·이용에 동의한다.
    동의 전까지 업무 API는 403 `privacy_consent_required`로 응답한다(`consent.require_present`).

    Args:
        payload: 이메일, 표시 이름, 전역 역할.
        admin: 계정을 만드는 최고관리자(감사 기록의 actor).

    Returns:
        UserCreated: 만든 계정과 재설정 링크 토큰. 토큰은 이 응답에서만 볼 수 있다.

    Raises:
        HTTPException: 같은 이메일의 계정이 있는 경우(409 `email_in_use`),
            줄 수 없는 역할인 경우(422 `invalid_role`).
    """
    if payload.global_role not in _VALID_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_role")
    # 로그인은 이메일을 그대로 비교하므로 받은 값으로 저장하고, 중복 검사만 대소문자를 무시한다.
    email = payload.email
    if await db.scalar(select(User.user_id).where(func.lower(User.email) == email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "email_in_use")
    user = User(
        email=email,
        display_name=payload.display_name,
        global_role=payload.global_role,
        approval_status="active",
        approved_by=admin.user_id,
        approved_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    audit.emit(
        db, event_type="user.created", target_kind="user", target_id=user.user_id,
        actor_user_id=admin.user_id,
        payload={"email": email, "global_role": payload.global_role},
    )
    await db.commit()
    await db.refresh(user)
    dto = _to_dto(user)
    reset = await issue_password_reset(db, user.user_id, admin)
    return UserCreated(user=dto, reset=reset)


async def issue_password_reset(
    db: AsyncSession, target_id: UUID, admin: User
) -> PasswordResetIssued:
    """최고관리자가 1회용 비밀번호 재설정 토큰을 발급한다.

    메일 발송 기능이 없으므로 관리자가 토큰을 발급해 본인에게 직접 전달한다.
    전달을 사람이 맡으므로 링크는 유효 시간이 짧고 한 번만 쓸 수 있다.

    응답의 `token`은 이 응답에서만 볼 수 있다.
    DB에는 해시만 남으므로 서버가 다시 보여 줄 수 없다.
    화면은 그 사실을 알리고, 다시 보려면 재발급하게 한다.

    기존 행을 지우고 새로 넣으므로 앞서 발급한 링크는 이 호출로 무효가 된다.
    삭제와 삽입은 한 트랜잭션에서 하고 `flush`로 순서를 강제한다.
    PK와 `token_hash` 유니크 제약이 둘 다 있어서 순서가 바뀌면 충돌한다.

    로그인할 수 없는 정지 계정에는 발급하지 않는다.
    링크로 비밀번호를 바꿔도 로그인할 수 없으므로 동작하는 것처럼 보이기만 한다.

    Args:
        target_id: 재설정 링크를 받을 사용자.
        admin: 발급하는 최고관리자(감사 기록의 actor).

    Returns:
        PasswordResetIssued: 평문 토큰, 만료 시각, 유효 시간(분).

    Raises:
        HTTPException: 사용자가 없는 경우(404), 로그인할 수 없는 계정인 경우(409 `user_not_active`).
    """
    user = await db.get(User, target_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")
    if user.approval_status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "user_not_active")
    return await issue_reset_token(db, user, issued_by=admin.user_id)


async def issue_reset_token(
    db: AsyncSession, user: User, *, issued_by: UUID | None
) -> PasswordResetIssued:
    """재설정 토큰 행을 새로 쓰고 감사 기록을 남긴 뒤 커밋한다.

    화면 발급(`issue_password_reset`)과 서버 명령(`api_gateway.cli.recover_admin`)이 같이 쓴다.
    대상 계정이 활성인지는 부르는 쪽이 확인한다.

    Args:
        user: 재설정 링크를 받을 활성 사용자.
        issued_by: 발급한 최고관리자. 서버 명령으로 발급하면 None이고, 감사 기록에
            `via: server_command`가 남는다.

    Returns:
        PasswordResetIssued: 평문 토큰, 만료 시각, 유효 시간(분).
    """
    secret = new_password_reset_secret()
    expires_at = datetime.now(UTC) + timedelta(minutes=RESET_LINK_VALID_MINUTES)
    await db.execute(
        delete(PasswordResetToken).where(PasswordResetToken.user_id == user.user_id)
    )
    await db.flush()
    db.add(
        PasswordResetToken(
            user_id=user.user_id,
            token_hash=hash_password_reset_secret(secret),
            expires_at=expires_at,
            issued_by=issued_by,
        )
    )
    # 토큰 값은 감사 기록에 남기지 않는다. 기록에는 발급자, 대상, 만료 시각만 남긴다.
    payload = {"valid_minutes": RESET_LINK_VALID_MINUTES, "expires_at": expires_at.isoformat()}
    if issued_by is None:
        payload["via"] = "server_command"
    audit.emit(
        db, event_type="user.password_reset_issued", target_kind="user",
        target_id=user.user_id, actor_user_id=issued_by, payload=payload,
    )
    await db.commit()
    return PasswordResetIssued(
        user_id=user.user_id, email=user.email, token=secret,
        expires_at=expires_at, valid_minutes=RESET_LINK_VALID_MINUTES,
    )
