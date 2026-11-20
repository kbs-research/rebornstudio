"""dev 토큰 발급 스크립트가 운영 셸에서 토큰을 만들지 않는지 보는 테스트.

이 스크립트는 인증을 우회하는 도구다. 운영 `DATABASE_URL`과 JWT 시크릿이 export된 셸에서 실수로 실행하면
운영 super_admin 토큰이 터미널에 찍힌다. 그래서 두 가지를 본다.
`ENVIRONMENT`가 없으면 기본값 prod라서 환경 확인에서 멈춘다. `ENVIRONMENT`를 local로 줘도 DB 호스트가
로컬이 아니면 DB에 붙기 전에 멈춘다. 스크립트를 별도 프로세스로 실행해 종료 코드와 거부 문구를 본다.
대상 주소는 연결되지 않는 주소만 쓴다.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "dev" / "dev_token.py"
_URL = "postgresql+asyncpg://reborn:secret-pw@db.example.invalid:5432/rebornstudio"


def _run(url: str, **env_over: str) -> subprocess.CompletedProcess:
    """`ENVIRONMENT`를 지운 셸에서 주어진 `DATABASE_URL`과 덮어쓸 값으로 스크립트를 실행한다."""
    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    env.update({"DATABASE_URL": url, "REBORN_ENV_FILE": "", **env_over})
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        env=env, capture_output=True, text=True, timeout=120, check=False,
    )


def test_dev_token_refuses_a_production_shell_without_environment() -> None:
    """운영 시크릿이 있는 셸에서 `ENVIRONMENT`가 빠지면 prod로 보고 멈추는지 확인한다. 비밀값은 출력하지 않는다."""
    secret = "prod-like-secret-value-0123456789abcdef"
    r = _run(_URL, JWT_SECRET=secret + "a", JWT_REFRESH_SECRET=secret + "b",
             CREDENTIALS_KEY=secret + "c", CORS_ORIGINS="", MAIU_ENABLED="true")
    assert r.returncode == 1, r.stderr
    assert "ENVIRONMENT=prod" in r.stderr, r.stderr
    assert secret not in r.stderr + r.stdout and "secret-pw" not in r.stderr


def test_dev_token_refuses_a_bare_shell_without_environment() -> None:
    """`ENVIRONMENT`도 시크릿도 없는 셸은 개발용 비밀값이 거절되어 토큰 없이 멈추는지 확인한다."""
    r = _run("postgresql+asyncpg://reborn:secret-pw@postgres:5432/rebornstudio")
    assert r.returncode == 1, r.stderr
    assert "ENVIRONMENT=local" in r.stderr, r.stderr
    assert "secret-pw" not in r.stderr and "reborn_refresh" not in r.stdout


def test_dev_token_refuses_remote_database_even_when_local() -> None:
    """`ENVIRONMENT=local`이어도 DB 호스트가 로컬이 아니면 DB에 붙기 전에 멈추고 호스트만 출력하는지 확인한다."""
    r = _run(_URL, ENVIRONMENT="local")
    assert r.returncode == 1, r.stderr
    assert "host=db.example.invalid" in r.stderr, r.stderr
    assert "secret-pw" not in r.stderr and "reborn_refresh" not in r.stdout


def _module():
    """스크립트를 모듈로 불러온다. 판정 함수만 직접 테스트한다."""
    spec = importlib.util.spec_from_file_location("dev_token_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(("url", "explicit", "refused"), [
    ("postgresql+asyncpg://u:p@localhost:5433/x", False, False),
    ("postgresql+asyncpg://u:p@127.0.0.1:5433/x", False, False),
    ("postgresql+asyncpg://u:p@[::1]:5433/x", False, False),
    ("postgresql+asyncpg://u:p@postgres:5432/x", True, False),
    ("postgresql+asyncpg://u:p@postgres:5432/x", False, True),
    ("postgresql+asyncpg://u:p@db.example.invalid:5432/x", True, True),
    ("postgresql+asyncpg://u:p@localhost.example.com:5432/x", True, True),
])
def test_db_host_rule(url: str, explicit: bool, refused: bool) -> None:
    """루프백은 늘 받고, compose 서비스 이름은 `ENVIRONMENT`를 명시했을 때만 받고, 그 밖은 거부하는지 확인한다."""
    assert (_module().db_host_refusal(url, environment_explicit=explicit) is not None) is refused
