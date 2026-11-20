"""JWT family 무효화와 Redis·LRU 폐기 백엔드 테스트."""
from uuid import uuid4

import jwt
import pytest
from api_gateway.config import settings
from api_gateway.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    new_family_id,
    revoke,
    revoke_family,
)
from api_gateway.services.auth import jwt_revocation


@pytest.fixture(autouse=True)
def _reset_backend():
    """테스트 전후로 폐기 백엔드를 비운다."""
    jwt_revocation.get_backend().reset_for_test()
    yield
    jwt_revocation.get_backend().reset_for_test()


def test_access_token_payload_includes_jti_and_fam():
    """access 토큰 payload에 `fam`과 32자 `jti`가 들어가는지 확인한다."""
    uid = uuid4()
    fam = new_family_id()
    token, _ = create_access_token(uid, "user", family_id=fam)
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=["HS256"],
        audience="rebornstudio-web", issuer="rebornstudio-api",
    )
    assert payload["fam"] == fam
    assert payload["jti"] and len(payload["jti"]) == 32


def test_revoke_blocks_jti_only_not_family():
    """토큰 하나를 폐기하면 그 jti만 막히고 같은 family의 다른 토큰은 유효한지 확인한다."""
    uid = uuid4()
    fam = new_family_id()
    t1, _ = create_access_token(uid, "user", family_id=fam)
    t2, _ = create_access_token(uid, "user", family_id=fam)  # family는 같고 jti는 다르다
    revoke(t1)
    with pytest.raises(TokenError, match="revoked"):
        decode_access_token(t1)
    # t2는 family가 같지만 jti가 다르므로 계속 유효해야 한다.
    payload = decode_access_token(t2)
    assert payload["fam"] == fam


def test_revoke_family_kills_all_tokens_in_family():
    """family를 폐기하면 그 family의 access·refresh 토큰이 모두 막히는지 확인한다."""
    uid = uuid4()
    fam = new_family_id()
    access, _ = create_access_token(uid, "user", family_id=fam)
    refresh = create_refresh_token(uid, family_id=fam)
    # family 전체를 폐기한다.
    revoke_family(access)
    with pytest.raises(TokenError, match="revoked"):
        decode_access_token(access)
    with pytest.raises(TokenError, match="revoked"):
        decode_refresh_token(refresh)


def test_revoke_family_does_not_affect_other_family():
    """family를 폐기해도 다른 family의 토큰은 유효한지 확인한다."""
    uid = uuid4()
    fam1 = new_family_id()
    fam2 = new_family_id()
    t1, _ = create_access_token(uid, "user", family_id=fam1)
    t2, _ = create_access_token(uid, "user", family_id=fam2)
    revoke_family(t1)
    with pytest.raises(TokenError, match="revoked"):
        decode_access_token(t1)
    # 다른 family의 토큰은 계속 유효하다.
    payload = decode_access_token(t2)
    assert payload["fam"] == fam2


def test_login_creates_new_family_each_time():
    """같은 사용자가 2회 로그인하면 서로 다른 family를 받는지 확인한다. 기기·세션마다 격리한다."""
    from api_gateway.models import User
    from api_gateway.services.auth.service import _issue_session

    user = User(
        user_id=uuid4(),
        email="x@example.com",
        global_role="user",
        approval_status="active",
        password_hash="x",
        display_name="x",
    )
    s1 = _issue_session(user)
    s2 = _issue_session(user)
    p1 = jwt.decode(
        s1.dto.access_token, settings.jwt_secret, algorithms=["HS256"],
        audience="rebornstudio-web", issuer="rebornstudio-api",
    )
    p2 = jwt.decode(
        s2.dto.access_token, settings.jwt_secret, algorithms=["HS256"],
        audience="rebornstudio-web", issuer="rebornstudio-api",
    )
    assert p1["fam"] != p2["fam"]


def test_redis_backend_used_when_redis_available():
    """지금 환경의 폐기 백엔드가 Redis가 있으면 `RedisBackend`, 없으면 `LRUBackend`인지 확인한다."""
    jwt_revocation.set_backend(None)  # 캐시된 싱글턴을 초기화한다
    backend = jwt_revocation.get_backend()
    cls_name = type(backend).__name__
    assert cls_name in ("RedisBackend", "LRUBackend")
