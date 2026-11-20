"""개발 super_admin 시드가 이미 있는 계정의 비밀번호를 명시했을 때만 바꾸는지 확인한다.

비밀번호를 명시하지 않은 재시드가 무작위 값으로 덮으면, 그 계정에 비밀번호로 로그인하던
사람이 `make seed-dev` 한 번에 들어가지 못한다.
"""
import secrets
import sys
from pathlib import Path

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import User
from api_gateway.security import verify_password
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts" / "dev"))
from seed_admin import seed_admin  # noqa: E402

pytestmark = pytest.mark.asyncio(loop_scope="session")

#: 테스트 세션 공용 admin과 겹치지 않는 테스트 전용 계정.
_EMAIL = "seed-admin-keep@test.local"


async def _hash() -> str:
    """테스트 계정의 현재 비밀번호 해시를 읽는다."""
    async with SessionLocal() as db:
        return await db.scalar(select(User.password_hash).where(User.email == _EMAIL))


async def test_reseed_without_password_keeps_existing_password(monkeypatch):
    """비밀번호 없이 다시 시드하면 기존 비밀번호가 그대로고, 명시하면 그 값으로 바뀐다."""
    monkeypatch.delenv("ADMIN_SEED_PASSWORD", raising=False)
    first = secrets.token_urlsafe(12)
    second = secrets.token_urlsafe(12)
    try:
        assert (await seed_admin(_EMAIL, first)).startswith("생성")
        assert verify_password(first, await _hash())

        assert (await seed_admin(_EMAIL)).startswith("갱신")
        assert verify_password(first, await _hash()), "명시하지 않은 재시드가 비밀번호를 바꿨다"

        await seed_admin(_EMAIL, second)
        assert verify_password(second, await _hash())
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.email == _EMAIL))
            await db.commit()


async def test_new_account_without_password_gets_a_random_one(monkeypatch):
    """새 계정은 비밀번호를 명시하지 않아도 빈 값이 아닌 무작위 해시를 받는다."""
    monkeypatch.delenv("ADMIN_SEED_PASSWORD", raising=False)
    try:
        assert (await seed_admin(_EMAIL)).startswith("생성")
        assert await _hash()
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.email == _EMAIL))
            await db.commit()
