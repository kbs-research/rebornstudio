"""비밀번호 해시 계산과 비교가 이벤트 루프 스레드 밖에서 도는지 보는 테스트.

bcrypt 비교 한 번은 수백 ms가 걸린다. API는 uvicorn 프로세스 하나라서 루프 스레드에서 비교하면
출근 시각에 로그인이 몰릴 때 모든 요청이 그 시간만큼 멈춘다.
"""
import threading

import pytest_asyncio
from api_gateway.services.auth import login_throttle, rate_limit
from api_gateway.services.auth import service as auth_service
from httpx import AsyncClient
from tests._helpers import make_account
from tests.conftest import uniq


@pytest_asyncio.fixture(autouse=True)
def _reset_state():
    """로그인 실패 잠금과 요청 한도를 테스트마다 비운다."""
    login_throttle.reset()
    rate_limit.reset_for_test()
    yield
    login_throttle.reset()
    rate_limit.reset_for_test()


def _recording(monkeypatch, name: str) -> list[int]:
    """서비스 모듈의 해시 함수를 감싸 호출된 스레드 번호를 모은다."""
    seen: list[int] = []
    real = getattr(auth_service, name)

    def wrapper(*args):
        """호출 스레드를 기록하고 실제 함수를 부른다."""
        seen.append(threading.get_ident())
        return real(*args)

    monkeypatch.setattr(auth_service, name, wrapper)
    return seen


async def test_login_verifies_password_off_the_loop(client: AsyncClient, admin_token: str, monkeypatch):
    """로그인의 비밀번호 비교가 루프 스레드가 아닌 스레드에서 도는지 확인한다."""
    _uid, email, pwd, _tok = await make_account(client, admin_token, "offloop")
    seen = _recording(monkeypatch, "verify_password")
    r = await client.post("/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, r.text
    assert seen and threading.get_ident() not in seen, "비밀번호 비교가 이벤트 루프를 막는다"


async def test_password_change_hashes_off_the_loop(client: AsyncClient, admin_token: str, monkeypatch):
    """비밀번호 변경의 비교와 새 해시 계산이 모두 루프 스레드 밖에서 도는지 확인한다."""
    _uid, _email, pwd, tok = await make_account(client, admin_token, "offchg")
    verified = _recording(monkeypatch, "verify_password")
    hashed = _recording(monkeypatch, "hash_password")
    r = await client.post(
        "/auth/password/change", headers={"Authorization": f"Bearer {tok}"},
        json={"old_password": pwd, "new_password": "OffLoopNew123!@x"},
    )
    assert r.status_code == 204, r.text
    loop_thread = threading.get_ident()
    assert verified and loop_thread not in verified
    assert hashed and loop_thread not in hashed


async def test_reset_confirm_hashes_off_the_loop(client: AsyncClient, admin_token: str, monkeypatch):
    """재설정 링크로 새 비밀번호를 정할 때 해시 계산이 루프 스레드 밖에서 도는지 확인한다."""
    created = await client.post(
        "/admin/users", headers={"Authorization": f"Bearer {admin_token}"},
        json={"email": uniq("offreset"), "display_name": "offreset"},
    )
    assert created.status_code == 201, created.text
    hashed = _recording(monkeypatch, "hash_password")
    r = await client.post(
        "/auth/password/reset/confirm",
        json={"token": created.json()["reset"]["token"], "new_password": "OffLoopReset123!@"},
    )
    assert r.status_code == 204, r.text
    assert hashed and threading.get_ident() not in hashed
