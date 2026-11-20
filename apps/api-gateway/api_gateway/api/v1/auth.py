"""가입·로그인·토큰 갱신·비밀번호 변경을 다루는 인증 API 라우터."""
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from reborn_contracts import (
    EmailLoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    PasswordResetCheck,
    PasswordResetConfirm,
    PasswordResetStatus,
    SessionDTO,
    UserDTO,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db, get_signed_in_user, oauth2_scheme
from api_gateway.models import User
from api_gateway.services import audit
from api_gateway.services.auth import rate_limit
from api_gateway.services.auth import service as auth_service
from api_gateway.services.auth.session_cookie import (
    clear_refresh_cookie,
    read_refresh_token,
    require_same_origin,
    set_refresh_cookie,
)

router = APIRouter()


@router.post("/login", response_model=LoginResponse, summary="이메일 로그인")
async def login(
    payload: EmailLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """이메일·비밀번호로 인증하고 access 토큰은 본문으로, refresh 토큰은 HttpOnly 쿠키로 준다.

    정지된 계정은 403 `account_suspended`를 받는다.
    접속 출처(IP·User-Agent)를 감사 이벤트에 남긴다. 보안 추적에 필요한 최소 정보다.
    IP별 한도는 틀린 비밀번호(401)만 센다. 같은 사무실의 성공한 로그인은 서로의 한도를 쓰지 않는다.
    """
    async with rate_limit.auth_failures("login", rate_limit.ip_key(request)):
        body, refresh_token = await auth_service.login(db, payload, origin=audit.origin(request))
    set_refresh_cookie(request, response, refresh_token)
    return body


@router.post("/password/change", status_code=status.HTTP_204_NO_CONTENT,
             summary="비밀번호 변경")
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """현재 비밀번호를 확인한 뒤 새 비밀번호로 바꾼다(로그인 필요).

    현재 비밀번호를 확인하는 경로라 로그인과 같은 연속 실패 잠금을 건다.
    분당 한도는 로그인한 사용자 단위로 틀린 비밀번호(401)만 센다.
    """
    key = rate_limit.user_key(user.user_id)
    async with rate_limit.auth_failures("password_change", key, counted=frozenset({401})):
        await auth_service.change_password(
            db, user, payload.old_password, payload.new_password, origin=audit.origin(request),
        )


@router.post("/password/reset/check", response_model=PasswordResetStatus,
             summary="비밀번호 재설정 링크 상태 확인")
async def check_password_reset(
    payload: PasswordResetCheck,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PasswordResetStatus:
    """재설정 링크가 아직 쓸 수 있는지와 만료 시각을 돌려준다. 아무것도 바꾸지 않는다.

    화면이 새 비밀번호를 받기 전에 부른다.
    토큰은 주소에 실으면 접속 기록에 남으므로 본문으로 받는다.
    토큰을 무차별 대입으로 찾는 데 쓰이지 않도록 IP별 실패 한도를 건다.
    쓸 수 없는 링크라는 응답(`valid=false`)을 실패로 센다. 버킷은 로그인과 따로라서
    로그인 실패가 몰린 사무실 IP도 재설정 링크를 확인할 수 있다.
    """
    async with rate_limit.auth_failures("reset_check", rate_limit.ip_key(request)) as tally:
        result = await auth_service.check_password_reset(db, payload.token)
        if not result.valid:
            await tally.fail()
    return result


@router.post("/password/reset/confirm", status_code=status.HTTP_204_NO_CONTENT,
             summary="비밀번호 재설정 확정")
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """super_admin이 준 링크의 토큰과 새 비밀번호로 비밀번호를 재설정한다.

    토큰 발급은 `POST /admin/users/{user_id}/password-reset`이 한다.
    메일 발송 기능이 없어서 사용자가 직접 재설정 링크를 요청하는 경로는 두지 않는다.
    쓸 수 없는 링크(400)를 IP별 실패 한도로 센다.
    """
    async with rate_limit.auth_failures("reset_confirm", rate_limit.ip_key(request)):
        await auth_service.confirm_password_reset(db, payload.token, payload.new_password)


@router.post("/refresh", response_model=SessionDTO, summary="JWT refresh (rotation)")
async def refresh(
    request: Request,
    response: Response,
    refresh_token: str | None = Body(default=None, embed=True),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_same_origin),
) -> SessionDTO:
    """refresh 쿠키를 검증·교체하고 새 access 토큰은 본문으로, 새 refresh 토큰은 쿠키로 준다.

    쿠키가 없으면 본문의 `refresh_token`을 받는다.
    refresh 토큰을 본문으로 보내는 클라이언트를 위한 호환 경로다.
    출처가 허용 출처가 아니면 403 `origin_not_allowed`다(`require_same_origin`).
    한도는 둘이다. 쿠키가 없거나 무효인 실패(401)는 IP별로 세고(`refresh_failures`),
    서명이 확인된 갱신은 로그인 세션별로 센다(`refresh_session_hit`). 넘으면 429와 `Retry-After`다.
    유효한 쿠키의 갱신은 IP 한도에 닿지 않으므로 한 사무실 IP 뒤의 여러 사람이 서로 막지 않는다.
    감사 이벤트 `auth.refreshed`의 `via`에 토큰을 받은 곳(`cookie`·`body`)을 남긴다.
    `via=body` 기록이 더 이상 없으면 호환 경로를 지워도 된다.
    """
    async with rate_limit.refresh_failures(request):
        token, via = read_refresh_token(request, refresh_token)
        if token is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token")
        issued = await auth_service.refresh(
            db, token, origin={**audit.origin(request), "via": via},
        )
    set_refresh_cookie(request, response, issued.refresh_token)
    return issued.dto


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="로그아웃 (token revoke)")
async def logout(
    request: Request,
    response: Response,
    refresh_token: str | None = Body(default=None, embed=True),
    access_token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_same_origin),
) -> None:
    """refresh 쿠키와 Authorization 헤더의 access 토큰을 폐기(revoke)하고 쿠키를 지운다.

    쿠키가 없으면 본문의 `refresh_token`을 받는다(refresh와 같은 호환 경로).
    토큰이 없거나 서명이 맞지 않으면 쿠키만 지운다.
    access 토큰을 폐기하지 않으면 로그아웃한 뒤에도 그 토큰이 만료까지 통한다.
    refresh 토큰의 사용자가 이 DB에 있으면 `auth.logout` 이벤트를 남긴다.
    없는 사용자의 토큰이어도 폐기하고 쿠키를 지운 뒤 204로 끝난다.
    출처가 허용 출처가 아니면 403 `origin_not_allowed`다.
    """
    token, _via = read_refresh_token(request, refresh_token)
    await auth_service.logout(db, token, access_token, origin=audit.origin(request))
    clear_refresh_cookie(request, response)


@router.get("/me", response_model=UserDTO, summary="내 정보")
async def me(user: User = Depends(get_signed_in_user)) -> UserDTO:
    """현재 인증된 사용자의 프로필 정보를 반환한다.

    개인정보 수집·이용에 동의하지 않은 사람도 부를 수 있다.
    화면은 `privacy_consented_at`이 비었으면 동의 화면을 먼저 표시한다.
    """
    return auth_service._to_dto(user)
