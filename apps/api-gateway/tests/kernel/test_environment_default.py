"""`ENVIRONMENT`가 없을 때 운영 규칙으로 뜨는지 보는 테스트.

배포가 `ENVIRONMENT`를 빠뜨리면 알려진 기본 JWT 시크릿으로 뜨는 일을 막는다. 기본값이 prod라서
비밀값 검사가 켜지고, 기본 시크릿이면 기동이 멈춘다. 로컬 개발은 `ENVIRONMENT=local`을 명시해야 뜬다.
"""
import pytest
from api_gateway.config import Settings

_SECRETS = ("JWT_SECRET", "JWT_REFRESH_SECRET", "CREDENTIALS_KEY")
_PROD_REQUIRED = ("CORS_ORIGINS", "MAIU_ENABLED", "ALLOW_FAKE_MAIU")


@pytest.fixture
def bare_env(monkeypatch):
    """`ENVIRONMENT`와 비밀값·운영 검사 대상을 지워 기본값만 남긴다."""
    for key in ("ENVIRONMENT", *_SECRETS, *_PROD_REQUIRED):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_missing_environment_means_prod_and_refuses_default_secrets(bare_env):
    """`ENVIRONMENT`가 없으면 prod로 보고 기본 비밀값을 거절하는지, 문구가 로컬 설정을 알리는지 확인한다."""
    with pytest.raises(ValueError, match="ENVIRONMENT=local"):
        Settings(_env_file=None)


def test_missing_environment_with_real_secrets_runs_as_prod(bare_env):
    """비밀값을 제대로 준 배포는 `ENVIRONMENT` 없이도 prod로 뜨는지 확인한다.

    CORS 기본값은 개발 주소(localhost)라서 운영 compose처럼 빈 값을 주고, 실제 MI를 켠다.
    """
    for key in _SECRETS:
        bare_env.setenv(key, "x" * 40 + key)
    bare_env.setenv("CORS_ORIGINS", "")
    bare_env.setenv("MAIU_ENABLED", "true")
    assert Settings(_env_file=None).environment == "prod"


@pytest.mark.parametrize("name", ["local", "test", "LOCAL"])
def test_local_and_test_skip_the_secret_check(bare_env, name):
    """로컬과 테스트는 이름을 명시하면 기본 비밀값으로 뜨는지 확인한다."""
    bare_env.setenv("ENVIRONMENT", name)
    assert Settings(_env_file=None).environment == name.lower()


def test_failure_message_does_not_echo_secrets(bare_env):
    """검사 실패 문구가 입력값(시크릿)을 싣지 않는지 확인한다. 실으면 기동 로그에 시크릿 일부가 남는다."""
    bare_env.setenv("JWT_SECRET", "short-but-secret-SENTINEL")
    with pytest.raises(ValueError) as e:
        Settings(_env_file=None)
    assert "SENTINEL" not in str(e.value)
