"""멤버 초대가 계정 유무를 드러내지 않고 이메일 대입을 늦추는지 보는 테스트.

프로젝트 관리자는 이메일만 알면 누구든 초대할 수 있다. 초대 실패 응답이 계정 유무를 가르면
이메일 목록으로 계정을 찾아낼 수 있다.
"""
import pytest_asyncio
from api_gateway.config import settings
from api_gateway.services.auth import rate_limit
from httpx import AsyncClient
from tests._helpers import add_member, make_account, new_project
from tests.conftest import uniq


@pytest_asyncio.fixture(autouse=True)
def _reset_state():
    """요청 한도를 테스트마다 비운다."""
    rate_limit.reset_for_test()
    yield
    rate_limit.reset_for_test()


def _h(tok: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def test_invite_failure_does_not_reveal_account(client: AsyncClient, admin_token: str):
    """없는 이메일과 정지된 계정의 초대 실패가 같은 응답인지 확인한다."""
    pid = await new_project(client, admin_token, "inv-enum")
    _, op = await add_member(client, admin_token, pid, "project_admin")
    susp_uid, susp_email, _pwd, _tok = await make_account(client, admin_token, "invenum")
    r = await client.patch(f"/admin/users/{susp_uid}/status", headers=_h(admin_token),
                           json={"status": "suspended"})
    assert r.status_code == 200, r.text

    ghost = await client.post(f"/projects/{pid}/members/invite", headers=_h(op),
                              json={"email": uniq("ghost"), "role": "annotator"})
    susp = await client.post(f"/projects/{pid}/members/invite", headers=_h(op),
                             json={"email": susp_email, "role": "annotator"})
    assert (ghost.status_code, ghost.json()) == (susp.status_code, susp.json())
    assert ghost.status_code == 404 and ghost.json()["detail"] == "invitee_unavailable"


async def test_invites_are_rate_limited_per_actor(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """한 사람이 분당 한도를 넘겨 초대하면 429인지 확인한다. 이메일 목록을 대입하는 속도를 늦춘다."""
    pid = await new_project(client, admin_token, "inv-rate")
    _, op = await add_member(client, admin_token, pid, "project_admin")
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(rate_limit, "INVITE_PER_MIN", 2)
    rate_limit.reset_for_test()
    codes = [
        (await client.post(f"/projects/{pid}/members/invite", headers=_h(op),
                           json={"email": uniq("rate"), "role": "annotator"})).status_code
        for _ in range(3)
    ]
    assert codes == [404, 404, 429]
