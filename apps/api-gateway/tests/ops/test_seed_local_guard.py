"""dev 시드가 로컬이 아닌 DB와 개발 밖 `ENVIRONMENT`에서 시작 전에 멈추는지 보는 테스트.

시드는 계정 비밀번호를 바꾸고 작업을 제출·승인한다. 셸에 운영 `DATABASE_URL`이 export돼 있으면
`os.environ.setdefault`가 덮지 않아 그 DB에 붙는다. 확인은 DB에 붙기 전에 끝나야 하므로
스크립트를 별도 프로세스로 실행해 종료 코드와 거부 문구를 본다.
대상 주소는 연결되지 않는 주소만 쓴다. 확인이 빠진 상태로 실행되어도 실제 DB에는 닿지 않는다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_DEV = Path(__file__).resolve().parents[4] / "scripts" / "dev"

#: 로컬 확인을 거치는 시드 스크립트와 인자 없이 실행해도 확인까지는 가는 최소 인자.
_SEEDS = [
    ("seed_demo.py", ["--status"]),
    ("seed_dev_clip.py", []),
    ("seed_dev_volume.py", []),
]


def _run(script: str, args: list[str], *, url: str, env_name: str) -> subprocess.CompletedProcess:
    """주어진 `DATABASE_URL`·`ENVIRONMENT`로 시드를 실행하고 결과를 돌려준다."""
    env = {
        **os.environ, "DATABASE_URL": url, "ENVIRONMENT": env_name, "REBORN_ENV_FILE": "",
    }
    return subprocess.run(
        [sys.executable, str(_DEV / script), *args],
        env=env, capture_output=True, text=True, timeout=120, check=False,
    )


@pytest.mark.parametrize(("script", "args"), _SEEDS, ids=[s for s, _ in _SEEDS])
def test_seed_refuses_remote_database(script: str, args: list[str]) -> None:
    """호스트가 로컬이 아니면 DB에 붙기 전에 멈추고 호스트만 출력한다."""
    url = "postgresql+asyncpg://reborn:secret-pw@db.example.invalid:5432/localhost_copy"
    r = _run(script, args, url=url, env_name="local")
    assert r.returncode != 0
    assert "로컬 DB가 아니라서 dev 시드를 멈춥니다: host=db.example.invalid" in r.stderr, r.stderr
    assert "secret-pw" not in r.stderr


@pytest.mark.parametrize(("script", "args"), _SEEDS, ids=[s for s, _ in _SEEDS])
def test_seed_refuses_non_dev_environment(script: str, args: list[str]) -> None:
    """`ENVIRONMENT`가 개발 값이 아니면 로컬 주소여도 멈춘다."""
    r = _run(
        script, args,
        url="postgresql+asyncpg://reborn:reborn@localhost:1/none", env_name="production",
    )
    assert r.returncode != 0
    assert "ENVIRONMENT=production이라서 dev 시드를 멈춥니다" in r.stderr, r.stderr
