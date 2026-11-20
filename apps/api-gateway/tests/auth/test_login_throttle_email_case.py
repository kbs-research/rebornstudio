"""로그인 실패 잠금이 이메일의 대소문자 변형으로 풀리지 않는지 보는 테스트.

`users.email`은 CITEXT라 대소문자가 다른 이메일도 같은 계정을 찾는다.
잠금 키가 받은 문자열 그대로면 변형마다 카운터가 따로 생겨 계정당 5회 잠금이 무력해진다.
"""
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import User
from api_gateway.services.auth import login_throttle
from httpx import AsyncClient
from tests._helpers import make_user
from tests.conftest import uniq


@pytest_asyncio.fixture(autouse=True)
def _reset_throttle():
    """로그인 실패 잠금을 테스트마다 비운다."""
    login_throttle.reset()
    yield
    login_throttle.reset()


async def test_lock_holds_across_email_case_variants(client: AsyncClient, admin_token: str):
    """소문자 이메일로 5회 실패한 뒤 대문자 변형으로 시도해도 429인지 확인한다."""
    uid, _ = await make_user(client, admin_token, "casevar")
    async with SessionLocal() as s:
        email = (await s.get(User, UUID(uid))).email
    for _ in range(5):
        r = await client.post("/auth/login", json={"email": email, "password": "Wrong123!@abcd"})
        assert r.status_code == 401, r.text
    local, domain = email.split("@")
    r = await client.post(
        "/auth/login", json={"email": f"{local.upper()}@{domain}", "password": "Wrong123!@abcd"},
    )
    assert r.status_code == 429, r.text
    assert r.json()["detail"] == "too_many_attempts"


def test_backend_keys_are_normalized():
    """모듈 공개 함수가 앞뒤 공백과 대소문자를 정규화해 같은 키로 세는지 확인한다."""
    email = uniq("normkey")
    for variant in (email, email.upper(), f"  {email}  ", email.title()):
        login_throttle.record_failure(variant)
    login_throttle.record_failure(email)
    assert login_throttle.retry_after(email.upper()) is not None
    login_throttle.clear(f" {email.upper()} ")
    assert login_throttle.retry_after(email) is None
