"""이메일 인증의 비즈니스 로직(login, refresh, logout, 비밀번호 변경·재설정).

계정은 최고관리자만 만든다(`admin.create_user`). 공개 가입은 없다.

보안 규칙:
- 없는 이메일과 틀린 비밀번호는 모두 401 invalid_credentials로 묶는다.
  계정 존재 여부를 드러내지 않는다.
- 비밀번호가 맞으면 계정 상태를 알린다. 정지된 계정은 403이다.
- refresh 토큰은 본문에 싣지 않는다. 라우터가 HttpOnly 쿠키로 준다(`session_cookie`).
- refresh 토큰을 교체(rotation)한 뒤 감사 이벤트를 남긴다. 교체는 로그인 시각(`auth_time`)을 옮기고,
  그 시각에서 세션 절대 수명이 지난 refresh는 401로 거절한다.
  폐기된 refresh 토큰이 유예 시간을 넘겨 다시 오면 family 전체를 끊는다(`refresh`).
- bcrypt 해시 계산과 비교는 `_off_loop`로 스레드에서 돈다. 한 번에 수백 ms가 걸리므로
  루프 스레드에서 돌리면 로그인이 몰릴 때 단일 프로세스 API의 모든 요청이 멈춘다.
"""
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from uuid import UUID

import anyio
from fastapi import HTTPException, status
from reborn_contracts import (
    EmailLoginRequest,
    LoginResponse,
    PasswordResetStatus,
    SessionDTO,
    UserDTO,
)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import PasswordResetToken, User
from api_gateway.security import (
    RevokedTokenError,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    hash_password_reset_secret,
    new_auth_time,
    refresh_auth_time,
    revoke,
    revoke_family,
    revoked_seconds_ago,
    session_lapsed,
    validate_password_policy,
    verify_password,
)
from api_gateway.services import audit
from api_gateway.services.auth import login_throttle, rate_limit

#: 비밀번호 정책을 어겼을 때의 문구. 변경·재설정·최초 설정이 모두 이 문장을 쓴다.
_PWD_POLICY_MSG = (
    "비밀번호는 12자 이상이어야 하고 대문자, 소문자, 숫자, 특수문자를 모두 포함해야 합니다"
)

async def _off_loop(fn, *args):
    """CPU를 오래 쓰는 해시 함수를 스레드에서 돌리고 결과를 돌려준다.

    호출부가 해시 함수를 부를 때마다 모듈 속성으로 찾으므로 테스트가 바꿔 끼운 함수가 적용된다.
    """
    return await anyio.to_thread.run_sync(fn, *args)


def _to_dto(user: User) -> UserDTO:
    """User 모델을 UserDTO로 바꾼다.

    `created_at`은 이미 읽어 온 값일 때만 싣고 아니면 None으로 둔다.
    방금 add한 행처럼 서버 기본값을 아직 읽지 않은 객체의 속성에 접근하면
    async 세션에서 암묵적 IO가 일어나 실패한다.
    """
    created_at = None if "created_at" in sa_inspect(user).unloaded else user.created_at
    return UserDTO(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        global_role=user.global_role,        approval_status=user.approval_status,        email_verified=user.email_verified,  # noqa: E501
        avatar_url=user.avatar_url,
        phone_number=user.phone_number,
        birth_date=user.birth_date,
        address=user.address,
        privacy_consented_at=user.privacy_consented_at,
        optional_privacy_consented_at=user.optional_privacy_consented_at,
        created_at=created_at,
    )


@dataclass(frozen=True)
class IssuedSession:
    """발급한 세션. 본문에 실을 access 토큰(`dto`)과 쿠키에 실을 refresh 토큰을 나눠 둔다."""
    dto: SessionDTO
    refresh_token: str


def _issue_session(
    user: User, family_id: str | None = None, auth_time: int | None = None,
) -> IssuedSession:
    """access·refresh 토큰을 같은 family와 같은 로그인 시각으로 발급한다.

    `family_id`와 `auth_time`이 None이면(login) 새 family와 지금 시각으로 시작한다.
    refresh 교체(rotation)는 이전 `family_id`와 `auth_time`을 유지한다.
    그래서 logout-all이 family 전체를 무효화할 수 있고, 교체가 세션 절대 수명을 늘리지 않는다.
    """
    from api_gateway.security import new_family_id
    fam = family_id or new_family_id()
    at = new_auth_time() if auth_time is None else auth_time
    access, exp = create_access_token(
        user.user_id, user.global_role, family_id=fam, auth_time=at,
    )
    refresh = create_refresh_token(user.user_id, family_id=fam, auth_time=at)
    return IssuedSession(
        dto=SessionDTO(access_token=access, access_expires_at=exp), refresh_token=refresh,
    )


async def change_password(
    db: AsyncSession, user: User, old_password: str, new_password: str,
    *, origin: dict | None = None,
) -> None:
    """로그인한 사용자의 비밀번호를 바꾼다. 현재 비밀번호와 정책을 확인한다.

    현재 비밀번호가 틀린 시도는 로그인 실패와 같은 잠금 카운터에 센다.
    카운터는 (이메일, IP) 쌍과 이메일 단위 두 가지다. IP는 `origin["ip"]`(`audit.client_ip`)다.
    이메일 단위 잠금이 걸리면 로그인과 같이 감사 기록을 남긴다(`_record_email_lock`).
    로그인만 잠그면 access 토큰을 가진 사람이 이 경로로 현재 비밀번호를 끝없이 맞혀 볼 수 있다.

    Raises:
        HTTPException: 연속 실패로 잠긴 경우(429 `too_many_attempts`, `Retry-After` 헤더),
            현재 비밀번호가 틀린 경우(401 `invalid_current_password`),
            새 비밀번호가 정책에 맞지 않는 경우(422).
    """
    ip = (origin or {}).get("ip")
    retry = login_throttle.retry_after(user.email, ip)
    if retry is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too_many_attempts",
            headers={"Retry-After": str(retry)},
        )
    if not user.password_hash or not await _off_loop(
        verify_password, old_password, user.password_hash,
    ):
        if login_throttle.record_failure(user.email, ip):
            _record_email_lock(db, user, origin, via="password_change")
            # 곧 401을 던져 요청 트랜잭션이 롤백되므로 기록을 먼저 확정한다.
            await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_current_password")
    if not validate_password_policy(new_password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, _PWD_POLICY_MSG)
    user.password_hash = await _off_loop(hash_password, new_password)
    user.password_changed_at = datetime.now(UTC)  # 이전에 발급한 토큰을 막는다
    audit.emit(
        db, event_type="user.password_changed", target_kind="user",
        target_id=user.user_id, actor_user_id=user.user_id,
    )
    await db.commit()


#: 재설정 링크 유효 시간(분).
#: 링크는 사람이 직접 전달하므로 대화방이나 메모에 오래 남지 않게 짧게 잡는다.
#: 화면은 이 값을 서버에서 받아 그대로 표시한다. 화면에 따로 적으면 두 값이 어긋난다.
#: 발급(`admin.issue_password_reset`)과 상태 확인(`check_password_reset`)이 같이 쓴다.
RESET_LINK_VALID_MINUTES = 30


async def check_password_reset(db: AsyncSession, token: str) -> PasswordResetStatus:
    """재설정 링크가 아직 쓸 수 있는지 읽는다. 행을 지우거나 바꾸지 않는다.

    화면이 새 비밀번호를 받기 전에 부른다.
    만료된 링크로 두 번 입력한 뒤에야 실패를 알게 되는 일을 막는다.
    판정은 `confirm_password_reset`과 같은 함수(`_reset_row_state`)가 한다.
    유효 시간은 발급과 같은 값(`RESET_LINK_VALID_MINUTES`)을 준다.
    """
    minutes = RESET_LINK_VALID_MINUTES
    if not token:
        return PasswordResetStatus(valid=False, valid_minutes=minutes)
    row, reason = await _reset_row_state(db, token)
    if reason is not None:
        return PasswordResetStatus(valid=False, valid_minutes=minutes, reason=reason)
    return PasswordResetStatus(valid=True, expires_at=row.expires_at, valid_minutes=minutes)


async def _reset_row_state(
    db: AsyncSession, token: str,
) -> tuple[PasswordResetToken | None, str | None]:
    """토큰의 행과 쓸 수 없는 이유(`used`·`expired`·`unknown`, 쓸 수 있으면 `None`)를 돌려준다.

    이미 쓴 링크가 만료 시각도 지났으면 `used`를 돌려준다. 링크를 받은 사람에게는 이미 썼다는
    사실이 더 정확한 안내다.
    """
    row = await db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == hash_password_reset_secret(token)
        )
    )
    if row is None:
        return None, "unknown"
    if row.used_at is not None:
        return row, "used"
    if row.expires_at <= datetime.now(UTC):
        return row, "expired"
    return row, None


async def confirm_password_reset(db: AsyncSession, token: str, new_password: str) -> None:
    """최고관리자가 준 링크의 토큰으로 새 비밀번호를 설정하고 로그인 실패 잠금을 푼다.

    토큰을 만드는 곳은 `services/auth/admin.issue_password_reset` 하나다.
    메일 발송 기능이 없으므로 사용자가 스스로 요청하는 경로는 두지 않는다.

    토큰은 DB 조회로 확인한다. 평문은 어디에도 없으므로 해시로 행을 찾고 만료를 본다.
    사용한 행은 지우지 않고 `used_at`을 적는다(1회용). 링크를 다시 연 사람에게
    "이미 쓴 링크"라고 알리려면 행이 남아 있어야 한다. 만료된 행도 같은 이유로 남긴다.
    사용자당 한 행이라 재발급이 덮어쓰므로 쌓이지 않는다.

    Args:
        token: 재설정 링크에 실린 1회용 비밀값.
        new_password: 정책 검사를 거칠 새 비밀번호.

    Raises:
        HTTPException: 토큰이 없거나 만료된 경우(400 `invalid_or_expired_token`),
            이미 쓴 토큰인 경우(400 `reset_link_used`),
            새 비밀번호가 정책에 맞지 않는 경우(422).
    """
    if not validate_password_policy(new_password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, _PWD_POLICY_MSG)
    row, reason = await _reset_row_state(db, token)
    if reason == "used":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "reset_link_used")
    if reason is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_or_expired_token")
    user = await db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_or_expired_token")
    now = datetime.now(UTC)
    # 사용 표시는 조건부 UPDATE 한 번으로 한다. 같은 링크로 두 요청이 동시에 와도 한쪽만 통과한다.
    # 읽은 행에 값을 대입하면 두 요청이 모두 "아직 안 씀"을 읽고 둘 다 비밀번호를 바꾼다.
    claimed = await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == row.user_id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "reset_link_used")
    user.password_hash = await _off_loop(hash_password, new_password)
    user.password_changed_at = now  # 기존 세션을 무효화한다
    login_throttle.clear_all(user.email)
    audit.emit(
        db, event_type="user.password_reset", target_kind="user",
        target_id=user.user_id, actor_user_id=user.user_id,
    )
    await db.commit()


async def _record_login_failure(
    db: AsyncSession, user: User | None, reason: str, origin: dict | None
) -> None:
    """실패한 로그인을 본인의 로그인 이력에 남긴다.

    성공만 남기면 본인이 이력에서 다른 사람의 로그인 시도를 확인할 수 없다.
    계정이 확인된 실패만 남긴다. 없는 이메일까지 남기면 기록이 오타와 스캔으로 차고,
    남는 이메일 자체도 개인정보다.
    429(연속 실패 잠금)는 남기지 않는다. 잠금 전까지의 실패가 이미 기록돼 있다.
    이메일 단위 잠금이 시작된 사실은 `_record_email_lock`이 한 줄로 남긴다.

    실패 경로라 호출자가 곧 401을 던진다. 트랜잭션이 롤백되지 않도록 여기서 커밋한다.
    """
    if user is None:
        return
    audit.emit(
        db, event_type="auth.login_failed", target_kind="user",
        target_id=user.user_id, actor_user_id=user.user_id,
        payload={**(origin or {}), "reason": reason},
    )
    await db.commit()


def _record_email_lock(
    db: AsyncSession, user: User | None, origin: dict | None, *, via: str,
) -> None:
    """이메일 단위 잠금이 걸린 계정에 `auth.login_locked` 감사 기록을 남긴다.

    commit은 호출부가 한다.
    이메일 단위 잠금은 맞는 비밀번호도 막고, 여러 IP에서 남의 이메일로 틀리기만 해도 걸린다.
    기록이 있어야 본인 로그인 이력과 관리자의 사용자 활동 이력에서 잠금을 확인하고
    비밀번호 재설정으로 풀 수 있다(재설정 확정이 `login_throttle.clear_all`을 부른다).
    잠금이 시작된 실패에서만 부르므로 잠금 하나에 기록이 하나다.
    행위자는 `auth.login_failed`와 같이 그 계정으로 둔다. 실제로 시도한 사람은 확인할 수 없다.
    없는 이메일은 막히는 계정이 없고 외래 키로 걸 사용자도 없어서 남기지 않는다.

    Args:
        user: 이메일로 찾은 계정. 없으면 아무것도 하지 않는다.
        origin: 잠금을 건 실패의 접속 출처(`audit.origin`). IP와 User-Agent만 옮긴다.
        via: 잠금을 건 경로. 로그인은 `login`, 비밀번호 변경은 `password_change`다.
    """
    if user is None:
        return
    src = origin or {}
    audit.emit(
        db, event_type="auth.login_locked", target_kind="user",
        target_id=user.user_id, actor_user_id=user.user_id,
        payload={
            "ip": src.get("ip"), "user_agent": src.get("user_agent"), "via": via,
            "failures": login_throttle.MAX_FAILS_EMAIL,
            "lock_seconds": login_throttle.LOCK_SECONDS,
        },
    )


@cache
def _dummy_hash() -> str:
    """해시 비교를 할 계정이 없을 때 대신 비교할 bcrypt 해시를 돌려준다. 처음 부를 때 한 번 만든다.

    없는 이메일만 bcrypt 비교를 건너뛰면 응답 시간 차이로 계정 존재 여부가 드러난다.
    """
    return hash_password(secrets.token_urlsafe(16))


async def login(
    db: AsyncSession, payload: EmailLoginRequest, *, origin: dict | None = None
) -> tuple[LoginResponse, str]:
    """이메일 로그인을 처리하고 (응답, 쿠키에 실을 refresh 토큰)을 돌려준다.

    없는 이메일과 틀린 비밀번호는 같은 401 invalid_credentials로 묶어 계정 존재 여부를
    드러내지 않는다.
    비밀번호가 맞으면 계정 상태를 알린다. 비밀번호를 아는 사람에게 상태를 숨길 이유가 없고,
    숨기면 정지된 사람이 비밀번호를 거듭 바꿔 시도하다 잠긴다.
    정지된 계정에는 403 `account_suspended`를 준다.
    연속 실패로 잠긴 (이메일, IP) 쌍이나 이메일에는 429 too_many_attempts와 `Retry-After` 헤더를
    준다. IP는 `origin["ip"]`(`audit.client_ip`)다.
    이메일 단위 잠금이 이번 실패로 걸리면 감사 기록을 남긴다(`_record_email_lock`).
    """
    # 연속 실패 잠금. Redis 공유 카운터이고 local/test는 인메모리다.
    ip = (origin or {}).get("ip")
    retry = login_throttle.retry_after(payload.email, ip)
    if retry is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too_many_attempts",
            headers={"Retry-After": str(retry)},
        )

    user = await db.scalar(select(User).where(User.email == payload.email))
    # 계정이 없거나 비밀번호가 없는 계정도 비교를 한 번 해서 응답 시간을 맞춘다.
    stored = user.password_hash if user and user.password_hash else None
    against = stored or await _off_loop(_dummy_hash)
    matched = await _off_loop(verify_password, payload.password, against)
    if stored is None or not matched:
        if login_throttle.record_failure(payload.email, ip):
            _record_email_lock(db, user, origin, via="login")
        await _record_login_failure(db, user, "bad_password", origin)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")

    login_throttle.clear(payload.email, ip)  # 자격증명이 맞으면 실패 카운터를 지운다
    if user.approval_status != "active":
        await _record_login_failure(db, user, "blocked", origin)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account_suspended")

    # 본인 로그인 이력(`/me/auth-events`)과 사용자 활동 이력이 읽는 기록이다.
    audit.emit(
        db, event_type="auth.login", target_kind="user",
        target_id=user.user_id, actor_user_id=user.user_id,
        payload=origin or {},
    )
    await db.commit()
    issued = _issue_session(user)
    return LoginResponse(user=_to_dto(user), session=issued.dto), issued.refresh_token


#: 폐기된 refresh 토큰이 다시 와도 재사용으로 보지 않는 시간(초).
#: 화면의 여러 탭은 같은 refresh 토큰을 함께 쓰므로 거의 동시에 갱신하면 한 탭만 새 세션을 받고
#: 나머지는 방금 폐기된 토큰을 보낸다. 유예 없이 재사용으로 보면 그 순간 모든 탭이 로그아웃된다.
#: 유예 안의 재사용은 401만 주고, 유예를 넘긴 재사용은 탈취로 보고 family 전체를 끊는다.
REFRESH_REUSE_GRACE_SECONDS = 30


async def _known_user_id(db: AsyncSession, sub: object) -> UUID | None:
    """토큰의 sub가 이 DB에 있는 사용자를 가리키면 그 id를, 아니면 None을 돌려준다.

    같은 서명 키를 쓰는 다른 스택이 발급한 토큰이나 지워진 사용자의 토큰도 서명 검증을 통과한다.
    감사 이벤트의 `actor_user_id`는 `users`의 외래 키라서 없는 사용자로 기록하면 commit이 실패하고
    로그아웃·갱신 응답이 500이 된다. 감사 기록을 남기기 전에 이 함수로 행위자를 확인한다.
    """
    try:
        uid = UUID(str(sub))
    except ValueError:
        return None
    return await db.scalar(select(User.user_id).where(User.user_id == uid))


async def _on_refresh_reuse(
    db: AsyncSession, refresh_token: str, payload: dict, origin: dict | None,
) -> None:
    """이미 폐기된 refresh 토큰이 다시 왔을 때 유예를 넘겼으면 family를 끊고 기록한다.

    교체로 폐기된 토큰이 한참 뒤에 다시 오면 그 토큰이 유출됐을 수 있다. 어느 쪽이 정당한
    사용자인지 서버는 가릴 수 없으므로 family 전체를 끊어 양쪽 모두 다시 로그인하게 한다.
    부르는 쪽은 결과와 관계없이 401을 준다.
    """
    jti = payload.get("jti")
    ago = revoked_seconds_ago(jti) if jti else None
    if ago is not None and ago <= REFRESH_REUSE_GRACE_SECONDS:
        return
    revoke_family(refresh_token)
    uid = await _known_user_id(db, payload.get("sub"))
    if uid is None:
        return
    audit.emit(
        db, event_type="auth.refresh_reused", target_kind="user", target_id=uid,
        actor_user_id=uid,
        payload={**(origin or {}), "revoked_seconds_ago": int(ago) if ago is not None else None},
    )
    await db.commit()


async def refresh(
    db: AsyncSession, refresh_token: str, *, origin: dict | None = None
) -> IssuedSession:
    """refresh 토큰을 검증하고 새 토큰으로 교체(rotation)한다.

    교체는 이전 jti의 폐기를 먼저 기록한 요청만 통과한다(`revoke`가 처음 폐기일 때만 참).
    같은 토큰으로 온 동시 요청이 모두 새 세션을 받으면 토큰 하나가 세션 여럿으로 갈라진다.
    이미 폐기된 jti의 토큰이 오면 `_on_refresh_reuse`가 유예 시간으로 재사용 여부를 판정한다.

    Raises:
        HTTPException: 토큰이 만료·취소됐거나 로그인 시각에서 세션 절대 수명이 지났거나
            계정이 비활성이거나 비밀번호 변경 전에 발급된 경우, 같은 토큰의 교체를 다른 요청이
            먼저 한 경우(401 `invalid_refresh_token`).
            한 로그인 세션의 갱신이 분당 한도를 넘은 경우
            (429 `rate_limit_exceeded:refresh_session`).
    """
    try:
        payload = decode_refresh_token(refresh_token)
        user_id = UUID(payload["sub"])
        family_id = payload.get("fam")  # 교체할 때 family를 유지한다
        auth_time = refresh_auth_time(payload)  # 교체할 때 로그인 시각을 유지한다
    except RevokedTokenError as e:
        if e.scope == "jti":
            await _on_refresh_reuse(db, refresh_token, e.payload, origin)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token") from None
    except (TokenError, ValueError, KeyError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token") from None
    # 토큰의 exp가 남아 있어도 절대 수명을 다시 본다.
    # 운영자가 수명을 줄이면 이미 발급한 토큰에도 적용된다.
    if auth_time is None or session_lapsed(auth_time):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token")
    # 서명을 확인한 뒤에 세션 단위로 센다. 한도를 넘으면 교체하지 않으므로 쿠키는 그대로 쓸 수 있다.
    await rate_limit.refresh_session_hit(family_id or str(user_id))

    user = await db.get(User, user_id)
    if not user or user.approval_status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token")
    # 비밀번호 변경·재설정 전에 발급한 refresh 토큰도 무효다.
    # access 가드와 같은 규칙으로 family를 막는다.
    pca = user.password_changed_at
    if pca and "iat" in payload and payload["iat"] < int(pca.timestamp()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token")

    # 이전 jti만 무효화하고 같은 family는 새 토큰으로 잇는다.
    # 다른 요청이 먼저 폐기했으면 이 요청은 교체하지 않는다.
    if not revoke(refresh_token):
        await _on_refresh_reuse(db, refresh_token, payload, origin)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token")
    audit.emit(
        db, event_type="auth.refreshed", target_kind="user", target_id=user.user_id,
        actor_user_id=user.user_id, payload=origin or {},
    )
    await db.commit()
    return _issue_session(user, family_id=family_id, auth_time=auth_time)


async def logout(
    db: AsyncSession, refresh_token: str | None, access_token: str | None,
    *, origin: dict | None = None,
) -> None:
    """토큰을 무효화하고 auth.logout 이벤트를 남긴다(로그인 이력의 근거).

    요청에 실린 토큰의 jti와 그 토큰이 속한 family를 함께 폐기한다.
    jti만 폐기하면 교체로 갈라진 같은 로그인의 다른 refresh 토큰이 살아남는다.
    탈취한 쪽이 먼저 교체한 경우 사용자가 로그아웃해도 탈취한 세션이 계속 이어진다.
    family는 로그인 한 번에 하나라서 같은 계정의 다른 로그인 세션은 유지된다.

    actor는 refresh 토큰의 sub에서 꺼낸다.
    토큰이 만료됐거나 이미 무효화됐거나 sub가 이 DB의 사용자가 아니면 이벤트를 남기지 않는다.
    토큰 폐기는 이벤트 기록과 관계없이 하므로 어떤 토큰이 와도 로그아웃은 실패하지 않는다.
    """
    sub: str | None = None
    if refresh_token:
        try:
            sub = decode_refresh_token(refresh_token).get("sub")
        except TokenError:
            sub = None
    for t in (refresh_token, access_token):
        if t:
            revoke(t)
            revoke_family(t)
    uid = await _known_user_id(db, sub) if sub else None
    if uid:
        audit.emit(
            db, event_type="auth.logout", target_kind="user",
            target_id=uid, actor_user_id=uid,
            payload=origin or {},
        )
        await db.commit()


async def logout_all(any_token: str) -> None:
    """한 토큰이 속한 family 전체를 무효화한다.

    부르는 라우트는 아직 없다. refresh 재사용 탐지는 이 함수가 아니라 `_on_refresh_reuse`가
    `revoke_family`를 직접 불러 한다.
    refresh·access 어느 토큰을 줘도 된다. 두 토큰에 같은 `fam` 클레임이 있다.
    """
    revoke_family(any_token)
