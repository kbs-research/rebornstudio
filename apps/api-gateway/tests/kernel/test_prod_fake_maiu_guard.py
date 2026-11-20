"""운영 환경이 가짜 MI 클라이언트로 기동하지 않는지 보는 테스트.

`MAIU_ENABLED=false`면 분석 요청을 가짜 클라이언트가 받아 그럴듯한 가짜 결과를 돌려준다.
운영에서 값이 빠지거나 잘못 들어가면 오류 없이 가짜 결과가 라벨링과 납품으로 흘러간다.
"""
import secrets

import pytest
from api_gateway.config import Settings


def _prod(**overrides) -> Settings:
    """비밀값을 채운 운영 설정을 만든다. 로컬 `.env`는 읽지 않는다."""
    base = {
        "environment": "prod",
        "jwt_secret": secrets.token_hex(32),
        "jwt_refresh_secret": secrets.token_hex(32),
        "credentials_key": secrets.token_hex(32),
        "cors_origins": "",
    }
    return Settings(_env_file=None, **{**base, **overrides})


def test_prod_refuses_to_start_with_the_fake_mi_client():
    """운영에서 `MAIU_ENABLED=false`면 설정 생성이 실패하는지 확인한다."""
    with pytest.raises(ValueError, match="maiu_enabled"):
        _prod(maiu_enabled=False)


def test_prod_runs_the_fake_mi_client_only_when_explicitly_allowed():
    """`ALLOW_FAKE_MAIU=true`를 함께 주면 가짜 MI로 기동하는지 확인한다."""
    assert _prod(maiu_enabled=False, allow_fake_maiu=True).maiu_enabled is False


def test_prod_with_the_real_mi_client_starts():
    """운영에서 `MAIU_ENABLED=true`면 그대로 기동하는지 확인한다."""
    assert _prod(maiu_enabled=True).maiu_enabled is True
