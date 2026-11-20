"""라우트가 공통으로 쓰는 FastAPI 의존성."""
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.database import SessionLocal
from api_gateway.logging_setup import actor_var
from api_gateway.models import User
from api_gateway.security import TokenError, decode_access_token
from api_gateway.services.auth import consent

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """요청 수명 동안 쓸 AsyncSession을 연다."""
    async with SessionLocal() as session:
        yield session


async def get_signed_in_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Bearer token을 검증하고 approval_status=active 사용자를 반환한다. 동의 여부는 보지 않는다.

    동의하지 않은 사람도 불러야 하는 경로만 이 의존성을 직접 쓴다(내 정보 조회, 동의 기록).
    나머지 경로는 동의까지 확인하는 `get_current_user`를 쓴다.

    Raises:
        HTTPException: 토큰이 없거나 유효하지 않거나 비밀번호 변경 전에 발급된 경우(401,
            마지막은 `token_superseded`). 계정이 활성이 아닌 경우(403).
    """
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
    try:
        payload = decode_access_token(token)
        user_id = UUID(payload["sub"])
    except (TokenError, ValueError, KeyError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token") from None
    user = await db.get(User, user_id)
    if not user or user.approval_status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account_not_active")
    # 비밀번호를 바꾸거나 재설정하기 전에 발급한 토큰은 무효로 한다.
    # 그래서 기존 세션은 로그아웃된다.
    pca = user.password_changed_at
    if pca and "iat" in payload and payload["iat"] < int(pca.timestamp()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token_superseded")
    # 이 요청의 이후 로그 줄에 actor를 붙인다. actor는 토큰을 해석한 뒤에 정해지므로 여기서 채운다.
    # 로그는 감사 기록보다 여러 곳에 오래 남으므로 개인정보를 줄이려고 이메일 대신 id를 쓴다.
    # Starlette의 `BaseHTTPMiddleware`는 `call_next`를 별도 태스크로 돌려서 여기서 `set`한
    # `contextvars` 값이 미들웨어에 전달되지 않는다. 그래서 같은 객체인 `request.state`에도 담는다.
    actor_var.set(str(user.user_id))
    request.state.actor = str(user.user_id)
    return user


async def get_current_user(user: User = Depends(get_signed_in_user)) -> User:
    """로그인하고 개인정보 수집·이용에 동의한 사용자를 반환한다. 업무 API가 쓰는 기본 의존성이다.

    동의 기록이 없는 계정은 동의 화면에서 동의한 뒤에 업무 API를 쓸 수 있다.
    판정은 `services.auth.consent`에 있다.

    Raises:
        HTTPException: `get_signed_in_user`의 경우와 같다.
            동의 기록이 없는 경우(403 `privacy_consent_required`).
    """
    consent.require_present(user)
    return user


async def require_super_admin(user: User = Depends(get_current_user)) -> User:
    """super_admin인 사용자만 통과시키고 그 User를 반환한다.

    Raises:
        HTTPException: global_role이 super_admin이 아닌 경우(403).
    """
    if user.global_role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient_permission")
    return user
