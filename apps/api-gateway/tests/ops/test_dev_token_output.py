"""dev 토큰 발급 스크립트의 출력이 쿠키 세션 구조에서 로그인으로 이어지는지 보는 테스트.

화면은 refresh 토큰을 HttpOnly 쿠키로만 받으므로 출력은 refresh 쿠키를 심는 콘솔 한 줄이다.
그 줄의 토큰으로 refresh가 되고, `--access` 출력은 API를 부를 수 있어야 한다.
스크립트를 별도 프로세스로 실행해 지금 테스트 DB의 super_admin으로 발급한다.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from httpx import AsyncClient
from tests._helpers import refresh_with_cookie

_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "dev" / "dev_token.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    """테스트 환경 그대로 스크립트를 실행하고 결과를 돌려준다."""
    env = {**os.environ, "ENVIRONMENT": "test", "REBORN_ENV_FILE": ""}
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--email", "admin@example.com", *args],
        env=env, capture_output=True, text=True, timeout=120, check=False,
    )


async def test_console_line_plants_refresh_cookie(client: AsyncClient, admin_token: str):
    """기본 출력이 로그아웃 뒤 refresh 쿠키를 심는 한 줄이고, 그 토큰으로 refresh가 되는지 확인한다."""
    r = _run()
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 2 and lines[0].startswith("# admin@example.com"), r.stdout
    line = lines[1]
    assert "localStorage" not in r.stdout
    assert line.startswith('fetch("/api/auth/logout",{method:"POST"})')
    m = re.search(r'document\.cookie="reborn_refresh=([^;]+); path=/api/auth; samesite=strict"', line)
    assert m, line
    assert (await refresh_with_cookie(client, m.group(1))).status_code == 200


async def test_access_flag_prints_a_bearer_token(client: AsyncClient, admin_token: str):
    """`--access` 출력이 토큰 한 줄이고 그 토큰으로 API를 부를 수 있는지 확인한다."""
    r = _run("--access")
    assert r.returncode == 0, r.stderr
    token = r.stdout.strip()
    assert "\n" not in token and token.count(".") == 2
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
