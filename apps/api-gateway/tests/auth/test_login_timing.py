"""없는 이메일의 로그인도 비밀번호 해시 비교를 한 번 하는지 보는 테스트.

bcrypt 비교는 수백 ms가 걸린다. 없는 이메일만 비교를 건너뛰면 응답 시간 차이로
계정 존재 여부가 드러난다. 응답 본문을 같게 해도 시간이 다르면 열거를 막지 못한다.
"""
import pytest_asyncio
from api_gateway.services.auth import login_throttle
from api_gateway.services.auth import service as auth_service
from httpx import AsyncClient
from tests.conftest import uniq


@pytest_asyncio.fixture(autouse=True)
def _reset_throttle():
    """로그인 실패 잠금을 테스트마다 비운다."""
    login_throttle.reset()
    yield
    login_throttle.reset()


async def test_unknown_email_still_runs_one_hash_check(client: AsyncClient, monkeypatch):
    """없는 이메일로 로그인해도 `verify_password`를 한 번 부르고 401인지 확인한다."""
    calls: list[str] = []
    real = auth_service.verify_password

    def counting(password: str, hashed: str) -> bool:
        """호출을 기록하고 실제 비교를 한다."""
        calls.append(hashed)
        return real(password, hashed)

    monkeypatch.setattr(auth_service, "verify_password", counting)
    r = await client.post(
        "/auth/login", json={"email": uniq("nobody"), "password": "Whatever123!@a"},
    )
    assert r.status_code == 401 and r.json()["detail"] == "invalid_credentials"
    assert len(calls) == 1, "없는 이메일이 해시 비교를 건너뛴다"
    assert calls[0].startswith("$2"), "비교 대상이 bcrypt 해시가 아니다"
