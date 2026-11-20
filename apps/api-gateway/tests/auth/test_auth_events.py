"""로그인·로그인 실패·로그아웃 이력 테스트. auth.* domain_events와 본인 이력 조회를 확인한다."""
from httpx import AsyncClient
from tests._helpers import (
    make_account,
    refresh_cookie_of,
    refresh_headers,
)
from tests.conftest import uniq


async def test_login_and_logout_recorded(client: AsyncClient, admin_token: str):
    """로그인하면 auth.login, 로그아웃하면 auth.logout이 본인 이력에 남는지 확인한다."""
    _uid, email, pwd, _tok = await make_account(client, admin_token, "authev")
    login = await client.post("/auth/login", json={"email": email, "password": pwd})
    sess = login.json()["session"]
    hdr = {"Authorization": f"Bearer {sess['access_token']}"}

    # 로그인 직후에는 auth.login이 기록된다.
    ev1 = (await client.get("/me/auth-events", headers=hdr)).json()
    assert any(e["event_type"] == "auth.login" for e in ev1), ev1

    # 로그아웃하면 auth.logout이 기록된다. 로그아웃은 같은 로그인의 access 토큰도 끊으므로
    # 이력은 다시 로그인한 세션으로 조회한다.
    r = await client.post("/auth/logout", headers=refresh_headers(refresh_cookie_of(login)))
    assert r.status_code == 204
    again = (await client.post("/auth/login", json={"email": email, "password": pwd})).json()
    hdr = {"Authorization": f"Bearer {again['session']['access_token']}"}
    ev2 = (await client.get("/me/auth-events", headers=hdr)).json()
    assert any(e["event_type"] == "auth.logout" for e in ev2), ev2


async def test_login_failure_recorded_on_own_history(client: AsyncClient, admin_token: str):
    """틀린 비밀번호로 로그인하면 본인 이력에 auth.login_failed가 남는지 확인한다.

    성공만 남기면 화면의 `로그인 이력`에 실패한 시도가 표시되지 않는다.
    그러면 아무도 로그인을 시도하지 않은 것처럼 읽힌다.
    """
    _uid, email, pwd, _tok = await make_account(client, admin_token, "authfail")
    r = await client.post("/auth/login", json={"email": email, "password": "WrongPass999!@"})
    assert r.status_code == 401

    login = await client.post("/auth/login", json={"email": email, "password": pwd})
    sess = login.json()["session"]
    hdr = {"Authorization": f"Bearer {sess['access_token']}"}
    ev = (await client.get("/me/auth-events", headers=hdr)).json()
    failed = [e for e in ev if e["event_type"] == "auth.login_failed"]
    assert failed, ev
    assert failed[0]["payload"]["reason"] == "bad_password"


async def test_login_failure_for_unknown_email_leaves_no_row(client: AsyncClient):
    """없는 이메일의 로그인 실패를 기록하지 않는지 확인한다.

    기록하면 오타와 스캔 시도로 로그가 가득 찬다. 이메일 자체도 개인정보다.
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import DomainEvent
    from sqlalchemy import func, select

    async def _failed_rows() -> int:
        """auth.login_failed 행 수를 센다."""
        async with SessionLocal() as s:
            return await s.scalar(
                select(func.count()).select_from(DomainEvent)
                .where(DomainEvent.event_type == "auth.login_failed")
            )

    before = await _failed_rows()
    r = await client.post(
        "/auth/login", json={"email": uniq("ghost"), "password": "Whatever123!@"}
    )
    assert r.status_code == 401
    assert await _failed_rows() == before, "없는 이메일의 실패가 기록됐다"
