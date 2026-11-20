"""JWT 발급·검증과 비밀번호 해시.

bcrypt의 72바이트 한계는 SHA-256 pre-hash로 피한다.
그래서 한글 같은 멀티바이트 비밀번호도 72바이트에서 잘리지 않는다.
JWT에는 secret rotation을 지원하려고 iat/iss/aud claim을 넣는다.
payload의 jti와 fam으로 폐기를 관리한다.
refresh 토큰의 `auth_time`은 로그인 시각이다. 교체 때 그대로 옮기고, 그 시각에서
`jwt_session_max_days`가 지나면 세션이 끝난다. 두 토큰의 만료도 그 시각을 넘지 않는다.
폐기 목록은 `jwt_revocation` backend(Redis/LRU)에 둔다.
`revoke()`는 jti 하나를, `revoke_family()`는 refresh family 전체를 무효로 한다.
family 폐기는 탈취를 탐지했을 때와 명시적으로 전체 로그아웃할 때 쓴다.
로그아웃할 때는 서명을 검증한 토큰만 폐기 목록에 등록한다.
검증하지 않은 문자열까지 등록하면 목록이 한없이 커져 DoS 공격 수단이 된다.
"""
import base64
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import bcrypt
import jwt

from api_gateway.config import settings
from api_gateway.services.auth import jwt_revocation

# 토큰 발급자(iss)와 토큰 종류별 대상(aud).
_ISS = "rebornstudio-api"
_AUD_ACCESS = "rebornstudio-web"
_AUD_REFRESH = "rebornstudio-refresh"

# 비밀번호 정책. 12자 이상, 대문자·소문자·숫자·특수문자를 하나 이상 포함한다.
_PWD_POLICY = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,}$")


class TokenError(Exception):
    """JWT 검증 실패를 하나로 나타내는 예외. PyJWT 예외를 감싼다."""


class RevokedTokenError(TokenError):
    """서명과 형식은 맞는데 폐기 목록에 있는 토큰인 경우의 예외.

    refresh 교체가 재사용을 판정하려면 서명이 맞는 토큰의 payload와 폐기 단위가 필요하다.

    Attributes:
        payload: 서명을 검증한 토큰 payload.
        scope: 폐기 단위. `jti`(토큰 하나)나 `family`(family 전체)다.
    """

    def __init__(self, payload: dict, scope: str) -> None:
        """payload와 폐기 단위를 담는다. 메시지는 `revoked`다."""
        super().__init__("revoked")
        self.payload = payload
        self.scope = scope


def validate_password_policy(password: str) -> bool:
    """비밀번호가 정책(12자 이상, 대문자·소문자·숫자·특수문자 포함)을 지키면 True를 반환한다."""
    return bool(_PWD_POLICY.match(password))


def _prep(password: str) -> bytes:
    """비밀번호를 SHA-256으로 먼저 해시하고 base64로 바꾼다.

    결과는 44바이트라 bcrypt의 72바이트 한계 안에 든다.
    passlib.hash.bcrypt_sha256과 같은 방식이다.
    """
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    """비밀번호를 bcrypt(SHA-256 pre-hash) 해시로 만든다.

    SHA-256 pre-hash로 bcrypt의 72바이트 한계를 피한다.
    """
    return bcrypt.hashpw(_prep(password), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    """입력 비밀번호가 저장된 bcrypt 해시와 맞는지 확인한다.

    해시 형식이 잘못되어 bcrypt가 ValueError를 내면 예외를 밖으로 내지 않고 False를 반환한다.
    """
    try:
        return bcrypt.checkpw(_prep(password), hashed.encode())
    except ValueError:
        return False


def _now() -> datetime:
    """현재 UTC 시각을 반환한다."""
    return datetime.now(UTC)


def _claims(sub: str, aud: str, exp: datetime, extra: dict | None = None) -> dict:
    """JWT payload를 만든다. iat/iss/aud/sub/exp를 넣고 extra를 덧붙인다."""
    now = _now()
    base = {
        "sub": sub,
        "iss": _ISS,
        "aud": aud,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if extra:
        base.update(extra)
    return base


def new_family_id() -> str:
    """refresh 토큰 family 식별자를 만든다. 로그인 때 만들고 refresh rotation 동안 유지한다."""
    return uuid4().hex


def new_auth_time() -> int:
    """로그인 시각 클레임 값(epoch 초)을 만든다. 로그인 때 만들고 refresh 교체 동안 유지한다."""
    return int(_now().timestamp())


def session_end(auth_time: int) -> datetime:
    """로그인 시각에서 세션 절대 수명(`jwt_session_max_days`)이 끝나는 시각을 돌려준다."""
    return datetime.fromtimestamp(auth_time, UTC) + timedelta(days=settings.jwt_session_max_days)


def refresh_auth_time(payload: dict) -> int | None:
    """refresh payload에서 로그인 시각을 꺼낸다. 값이 없거나 정수가 아니면 None이다.

    `auth_time`이 없는 토큰은 발급 시각(`iat`)을 로그인 시각으로 보고 그때부터 절대 수명을 센다.
    클레임이 없다고 거절하면 그 토큰을 가진 사용자가 한꺼번에 로그아웃된다.
    """
    value = payload.get("auth_time", payload.get("iat"))
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def session_lapsed(auth_time: int) -> bool:
    """로그인 시각에서 세션 절대 수명이 지났으면 True를 반환한다."""
    return _now() >= session_end(auth_time)


def _expiry(ttl: timedelta, auth_time: int | None) -> datetime:
    """지금부터 `ttl` 뒤와 세션 끝 중 이른 시각을 만료로 돌려준다.

    로그인 시각이 없으면 `ttl`만 쓴다.
    """
    expires_at = _now() + ttl
    if auth_time is None:
        return expires_at
    return min(expires_at, session_end(auth_time))


def create_access_token(
    user_id: UUID, role: str, family_id: str | None = None, auth_time: int | None = None,
) -> tuple[str, datetime]:
    """access token을 발급한다. jti와 fam을 넣고, 설정한 분(기본 15분) 뒤에 만료된다.

    Args:
        family_id: 같은 로그인 세션을 공유하는 refresh family. None이면 새 family.
        auth_time: 세션의 로그인 시각. 있으면 만료가 세션 끝을 넘지 않는다.

    Returns:
        (token 문자열, 만료 datetime) 튜플.
    """
    expires_at = _expiry(timedelta(minutes=settings.jwt_access_ttl_minutes), auth_time)
    fam = family_id or new_family_id()
    token = jwt.encode(
        _claims(str(user_id), _AUD_ACCESS, expires_at,
                {"role": role, "type": "access", "jti": uuid4().hex, "fam": fam}),
        settings.jwt_secret,
        algorithm="HS256",
    )
    return token, expires_at


def create_refresh_token(
    user_id: UUID, family_id: str | None = None, auth_time: int | None = None,
) -> str:
    """refresh token을 발급한다. jti·fam·auth_time을 넣는다.

    만료는 설정한 일수(`jwt_refresh_ttl_days`) 뒤와 세션 끝(`auth_time` + `jwt_session_max_days`) 중
    이른 시각이다. 그래서 교체를 반복해도 세션이 로그인 시각에서 정한 기간을 넘지 않는다.

    Args:
        family_id: 같은 로그인 세션을 공유하는 refresh family. None이면 새 family.
        auth_time: 세션의 로그인 시각(epoch 초). None이면 지금을 로그인 시각으로 쓴다.
    """
    at = new_auth_time() if auth_time is None else auth_time
    expires_at = _expiry(timedelta(days=settings.jwt_refresh_ttl_days), at)
    fam = family_id or new_family_id()
    return jwt.encode(
        _claims(str(user_id), _AUD_REFRESH, expires_at,
                {"type": "refresh", "jti": uuid4().hex, "fam": fam, "auth_time": at}),
        settings.jwt_refresh_secret,
        algorithm="HS256",
    )


def _decode(token: str, secret: str, audience: str) -> dict:
    """PyJWT decode를 감싸고 디코드 실패를 모두 TokenError로 바꾼다.

    깊게 중첩한 JSON 헤더·본문은 PyJWT 버전에 따라 PyJWTError가 아니라 RecursionError나
    ValueError로 올라온다. 그 예외를 그대로 두면 인증 실패가 401이 아니라 500이 된다.
    """
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], audience=audience, issuer=_ISS)
    except (jwt.PyJWTError, RecursionError, ValueError) as e:
        raise TokenError(str(e)) from e


def _check_revoked(payload: dict) -> None:
    """jti와 fam이 폐기됐는지 backend에서 확인한다. 폐기됐으면 RevokedTokenError를 낸다.

    family 폐기를 먼저 본다. family가 끊긴 토큰은 jti 재사용으로 다시 판정하지 않는다.
    """
    backend = jwt_revocation.get_backend()
    if (fam := payload.get("fam")) and backend.is_family_revoked(fam):
        raise RevokedTokenError(payload, "family")
    if (jti := payload.get("jti")) and backend.is_jti_revoked(jti):
        raise RevokedTokenError(payload, "jti")


def decode_access_token(token: str) -> dict:
    """access token을 검증하고 payload dict를 반환한다.

    Raises:
        TokenError: 토큰이 만료됐거나 서명·type이 맞지 않거나 jti·fam이 폐기된 경우.
    """
    payload = _decode(token, settings.jwt_secret, _AUD_ACCESS)
    if payload.get("type") != "access":
        raise TokenError("wrong type")
    _check_revoked(payload)
    return payload


def decode_refresh_token(token: str) -> dict:
    """refresh token을 검증하고 payload dict를 반환한다.

    Raises:
        TokenError: 토큰이 만료됐거나 서명·type이 맞지 않거나 jti·fam이 폐기된 경우.
    """
    payload = _decode(token, settings.jwt_refresh_secret, _AUD_REFRESH)
    if payload.get("type") != "refresh":
        raise TokenError("wrong type")
    _check_revoked(payload)
    return payload


def new_password_reset_secret() -> str:
    """관리자가 발급하는 재설정 링크에 넣을 1회용 비밀값을 만든다.

    재설정 토큰은 서버가 취소할 수 있어야 해서 JWT 대신 난수를 쓴다.
    stateless 토큰은 이전 토큰을 무효로 하거나 유효 시간을 줄일 수 없다.
    그래서 상태는 DB(`password_reset_tokens`)에 두고 이 난수로 행을 찾는다.
    """
    return secrets.token_urlsafe(32)


def hash_password_reset_secret(secret: str) -> str:
    """재설정 비밀값의 sha256 hex를 만든다. DB에는 평문 대신 이 값만 저장한다.

    확인할 때도 이 값으로 조회한다. 평문을 꺼내 비교하는 경로는 두지 않는다.
    """
    return hashlib.sha256(secret.encode()).hexdigest()


def _decode_either_no_exp(token: str) -> dict | None:
    """토큰 서명을 검증하고 payload를 반환한다. 만료는 무시하고, 검증에 실패하면 None을 반환한다.

    폐기 목록에 등록하기 전에 형식을 확인하고 jti/fam을 꺼낼 때 쓴다.
    """
    for secret in (settings.jwt_secret, settings.jwt_refresh_secret):
        for aud in (_AUD_ACCESS, _AUD_REFRESH):
            try:
                return jwt.decode(
                    token, secret, algorithms=["HS256"],
                    audience=aud, issuer=_ISS,
                    options={"verify_exp": False},
                )
            except jwt.PyJWTError:
                continue
    return None


def revoke(token: str) -> bool:
    """서명을 검증한 토큰의 jti를 폐기 목록에 등록한다. 이번 호출이 처음 등록했으면 True다.

    검증에 실패한 토큰은 등록하지 않는다. 검증하지 않은 문자열까지 등록하면 목록이 한없이 커져
    DoS 공격 수단이 된다. 등록하지 않았거나 이미 폐기된 토큰이면 False다.
    refresh 교체는 이 값으로 같은 토큰의 동시 교체를 하나만 통과시킨다.
    """
    if not token:
        return False
    payload = _decode_either_no_exp(token)
    if not payload:
        return False
    jti = payload.get("jti")
    if not jti:
        return False
    # exp까지 남은 초를 TTL로 쓴다. `_TTL_MIN`(60초)보다 짧으면 backend가 그 값으로 올린다.
    ttl = _ttl_until(payload.get("exp"))
    return jwt_revocation.get_backend().revoke_jti(jti, ttl)


def revoked_seconds_ago(jti: str) -> float | None:
    """jti가 폐기된 뒤 지난 초를 돌려준다. 폐기되지 않았으면 None이다."""
    at = jwt_revocation.get_backend().jti_revoked_at(jti)
    if at is None:
        return None
    return max(0.0, _now().timestamp() - at)


def revoke_family(token: str) -> None:
    """토큰의 family 전체를 폐기한다(탈취 탐지, 명시적 전체 로그아웃)."""
    if not token:
        return
    payload = _decode_either_no_exp(token)
    if not payload:
        return
    fam = payload.get("fam")
    if not fam:
        return
    # family의 토큰 중 refresh가 가장 오래 유효하므로 refresh TTL을 쓴다.
    ttl = settings.jwt_refresh_ttl_days * 86400
    jwt_revocation.get_backend().revoke_family(fam, ttl)


def _ttl_until(exp: int | None) -> int:
    """exp 타임스탬프까지 남은 초를 반환한다. 이미 지났으면 0을 반환한다."""
    if not exp:
        return 0
    return max(0, exp - int(_now().timestamp()))
