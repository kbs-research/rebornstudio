"""보안 회귀 테스트. 비밀번호 해시, 토큰 폐기와 디코드, 로그인 오류, 운영 설정, CORS를 확인한다."""

from api_gateway.config import settings
from api_gateway.security import (
    TokenError,
    decode_access_token,
    hash_password,
    revoke,
    verify_password,
)
from httpx import AsyncClient
from tests.conftest import uniq

# bcrypt 앞에 SHA-256 pre-hash를 두어 72바이트에서 한글이 잘리는 문제를 피한다

def test_password_handles_long_utf8():
    """한글이 섞인 72바이트 초과 비밀번호도 정확히 검증하는지 확인한다."""
    pw = "강력한비밀번호테스트용한국어1!"  # 16자, 44바이트(한글 3바이트, 영숫자 1바이트)
    long_pw = pw + "추가padding문자열매우긴!1A"  # 더하면 72바이트를 넘는다
    h = hash_password(long_pw)
    assert verify_password(long_pw, h)
    # 다른 비밀번호는 거부한다
    assert not verify_password(long_pw + "x", h)
    assert not verify_password(pw, h)


def test_password_truncation_not_silently_accepted():
    """72바이트 뒤만 다른 비밀번호가 검증에 실패하는지 확인한다. 72바이트에서 자르면 앞부분만 같아도 통과한다."""
    base = "X" * 72 + "A"
    diff = "X" * 72 + "B"
    h = hash_password(base)
    # 뒷부분이 다르면 해시도 달라서 검증이 실패해야 한다.
    assert not verify_password(diff, h)


# 로그아웃 DoS를 막으려고 검증되지 않은 토큰은 폐기 목록에 넣지 않는다

def test_revoke_ignores_garbage():
    """JWT가 아닌 문자열을 revoke()에 넘겨도 백엔드에 jti가 등록되지 않는지 확인한다.

    백엔드 종류(LRU·Redis)와 무관하게 보려고 등록 호출을 기록하는 백엔드로 바꿔 끼운다.
    서명이 맞는 토큰은 등록되는지도 같이 봐서 기록 백엔드가 호출을 놓치지 않음을 확인한다.
    """
    from uuid import uuid4

    from api_gateway.security import create_access_token
    from api_gateway.services.auth import jwt_revocation

    class _Recorder(jwt_revocation.LRUBackend):
        """revoke_jti 호출을 모으는 테스트용 백엔드."""

        def __init__(self) -> None:
            """빈 기록으로 시작한다."""
            super().__init__()
            self.calls: list[str] = []

        def revoke_jti(self, jti: str, ttl_seconds: int) -> None:
            """호출을 기록하고 LRU에도 등록한다."""
            self.calls.append(jti)
            super().revoke_jti(jti, ttl_seconds)

    original = jwt_revocation.get_backend()
    rec = _Recorder()
    jwt_revocation.set_backend(rec)
    try:
        revoke("not-a-jwt-at-all")
        revoke("")
        revoke("a" * 10000)
        assert rec.calls == []
        tok, _ = create_access_token(uuid4(), role="user")
        revoke(tok)
        assert len(rec.calls) == 1
    finally:
        jwt_revocation.set_backend(original)


# JWT claim에 iat·iss·aud가 들어간다

def test_access_token_has_iss_aud_iat():
    """access token에 iss·aud·iat와 type이 들어가는지 확인한다."""
    from uuid import uuid4

    from api_gateway.security import create_access_token
    tok, _ = create_access_token(uuid4(), role="user")
    payload = decode_access_token(tok)
    assert payload["iss"] == "rebornstudio-api"
    assert payload["aud"] == "rebornstudio-web"
    assert "iat" in payload
    assert payload["type"] == "access"


def test_wrong_audience_rejected():
    """다른 audience로 발급된 token의 decode가 실패하는지 확인한다."""
    import jwt as pyjwt
    bad = pyjwt.encode(
        {"sub": "x", "iss": "rebornstudio-api", "aud": "wrong-audience",
         "exp": 9999999999, "type": "access"},
        settings.jwt_secret, algorithm="HS256",
    )
    try:
        decode_access_token(bad)
        raise AssertionError("should have raised")
    except TokenError:
        pass


# 로그인은 모든 실패에 401 invalid_credentials를 돌려준다(계정 존재 여부 노출 차단)

async def test_login_wrong_password_generic_401(client: AsyncClient):
    """존재하는 관리자 계정에 틀린 비밀번호를 보내면 401(invalid_credentials)인지 확인한다.

    구체적인 오류를 노출하지 않는다.
    """
    r = await client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "Wrong1234!@a"},
    )
    assert r.status_code == 401


async def test_login_nonexistent_email_same_response(client: AsyncClient):
    """존재하지 않는 이메일도 같은 401을 받는지 확인한다. 계정 존재 여부를 알 수 없게 한다."""
    r = await client.post(
        "/auth/login",
        json={"email": uniq("ghost"), "password": "Anything123!@a"},
    )
    assert r.status_code == 401


# prod 환경에서는 기본 secret을 거부한다

def test_prod_config_rejects_dev_secrets():
    """environment=production과 기본 secret을 함께 쓰면 Settings 인스턴스 생성이 실패하는지 확인한다."""
    import os

    import pytest
    from api_gateway.config import Settings
    saved = dict(os.environ)
    try:
        # conftest가 설정한 ENVIRONMENT=test를 production으로 바꾼다.
        os.environ["ENVIRONMENT"] = "production"
        with pytest.raises(ValueError, match="must be set"):
            Settings()
    finally:
        os.environ.clear()
        os.environ.update(saved)


# CORS preflight

async def test_cors_preflight_allows_listed_origin(client: AsyncClient):
    """허용 목록의 origin만 CORS preflight를 통과하고 메서드는 명시 목록만 허용하는지 확인한다."""
    r = await client.options(
        "/healthz",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
    # 메서드는 와일드카드가 아니라 명시 목록만 허용한다.
    methods = r.headers.get("access-control-allow-methods", "")
    assert "GET" in methods
    assert "*" not in methods

    # 목록에 없는 origin은 같은 요청이 400이고 허용 헤더가 없다.
    denied = await client.options(
        "/healthz",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


# /readyz 오류 메시지에 DB 정보를 노출하지 않는다

def test_readyz_error_message_is_generic():
    """readyz가 일반 오류 문구만 노출하는지 확인한다. 실제 DB 중단을 재현하기 어려워 소스 코드 검사로 대신한다."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "api_gateway" / "main.py"
    text = src.read_text()
    # 원본 예외를 노출하지 않는다.
    assert 'f"db_unavailable: {e}"' not in text
    assert '"db_unavailable"' in text


# PyJWT를 쓰고 python-jose import가 남아 있지 않다

def test_no_python_jose_imports():
    """api_gateway 코드에 python-jose import가 남아 있지 않은지 확인한다.

    경로는 이 파일 위치에서 계산한다. 검사한 파일 수도 확인해 경로가 틀려 빈 폴더를 검사하면 실패한다.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "api_gateway"
    files = list(root.rglob("*.py"))
    assert len(files) > 100, f"검사 대상 파일이 너무 적다: {root} ({len(files)}개)"
    pattern = re.compile(r"^\s*(from jose\b|import jose\b)", re.MULTILINE)
    hits = [str(f) for f in files if pattern.search(f.read_text(encoding="utf-8"))]
    assert hits == [], f"python-jose 잔존: {hits}"


# 깊게 중첩한 JSON 헤더를 담은 토큰은 500이 아니라 401로 거절한다

def _nested_header_token(depth: int = 1000) -> str:
    """헤더 세그먼트가 여는 대괄호 depth개인 토큰을 만든다. 서명은 검증 전에 실패하므로 임의 값이다."""
    import base64

    header = base64.urlsafe_b64encode(b"[" * depth).rstrip(b"=").decode()
    return f"{header}.e30.sig"


async def test_nested_header_token_is_401(client: AsyncClient):
    """중첩 헤더 토큰으로 /auth/me를 부르면 401인지 확인한다. RecursionError가 새면 500이 된다."""
    r = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {_nested_header_token()}"},
    )
    assert r.status_code == 401, r.text


def test_decode_maps_recursion_and_value_errors(monkeypatch):
    """PyJWT가 RecursionError·ValueError를 그대로 올려도 TokenError로 바뀌는지 확인한다.

    PyJWT 버전마다 감싸는 예외가 다르므로 라이브러리와 무관하게 `_decode`의 변환을 본다.
    """
    import jwt
    import pytest

    for exc in (RecursionError("deep"), ValueError("bad")):
        def _raise(*_a, _exc=exc, **_k):
            """미리 고른 예외를 던진다."""
            raise _exc

        monkeypatch.setattr(jwt, "decode", _raise)
        with pytest.raises(TokenError):
            decode_access_token("x.y.z")
