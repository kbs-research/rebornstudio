"""멤버 목록의 연락처·개인정보를 역할별로 가리는지 보는 테스트.

이메일과 필수 동의 시각은 관리자에게만 싣는다. 검수자는 목록을 받지만 그 필드가 비어 있다.
선택 항목(전화번호·생년월일·주소)은 수집하지 않으므로 관리자에게도 싣지 않는다.
"""
from datetime import date
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import User
from httpx import AsyncClient
from sqlalchemy import update
from tests._helpers import add_member, new_project

_PERSONAL = ("email", "privacy_consented_at")
_OPTIONAL = ("phone_number", "birth_date", "address", "optional_privacy_consented_at")


def _h(tok: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def test_personal_fields_only_for_managers(client: AsyncClient, admin_token: str):
    """관리자는 이메일을 받고 선택 항목은 받지 않으며, 검수자는 이메일이 null이고, 라벨러는 403인지 확인한다."""
    pid = await new_project(client, admin_token, "pii-role")
    lab_id, lab = await add_member(client, admin_token, pid, "annotator")
    # 이전 버전에서 남은 선택 항목 값이 있어도 멤버 응답에 실리지 않아야 한다.
    async with SessionLocal() as s:
        await s.execute(
            update(User).where(User.user_id == UUID(lab_id))
            .values(phone_number="010-0000-0000", birth_date=date(1990, 1, 2), address="서울")
        )
        await s.commit()
    _, pa = await add_member(client, admin_token, pid, "project_admin")
    _, rev = await add_member(client, admin_token, pid, "reviewer")

    url = f"/projects/{pid}/members"
    for tok in (admin_token, pa):
        r = await client.get(url, headers=_h(tok))
        assert r.status_code == 200, r.text
        row = next(m for m in r.json() if m["user_id"] == lab_id)
        assert row["email"]
        for key in _OPTIONAL:
            assert key not in row, (key, row)

    for tok in (rev,):
        r = await client.get(url, headers=_h(tok))
        assert r.status_code == 200, r.text
        rows = r.json()
        assert any(m["user_id"] == lab_id for m in rows)
        for m in rows:
            for key in _PERSONAL:
                assert m[key] is None, (key, m)
            for key in _OPTIONAL:
                assert key not in m, (key, m)
            # 이름·역할은 남는다.
            assert m["role"]

    r = await client.get(url, headers=_h(lab))
    assert r.status_code == 403
