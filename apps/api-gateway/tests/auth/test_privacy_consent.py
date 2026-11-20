"""개인정보 수집·이용 동의 테스트.

관리자가 만든 계정은 동의 기록이 없고, 동의하기 전까지 업무 API를 쓰지 못한다.
필수 동의 철회는 따로 받지 않는다.
새 선택 동의와 선택 항목 입력은 받지 않는다.
이미 선택 동의한 계정은 언제든 철회할 수 있고 철회하면 선택 항목을 바로 지운다.
화면(동의 화면·내 계정)이 안내하는 동작을 서버가 지키는지 확인한다.
"""
import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

from api_gateway.database import SessionLocal, engine
from api_gateway.models import DomainEvent, User
from httpx import AsyncClient
from sqlalchemy import select, update
from tests._helpers import SAME_ORIGIN, make_user
from tests.conftest import uniq


def _h(token: str) -> dict[str, str]:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _me(client: AsyncClient, hdr: dict) -> dict:
    """호출한 사용자의 계정 정보를 돌려준다."""
    return (await client.get("/auth/me", headers=hdr)).json()


async def _consent_vias(uid: str, event_type: str = "user.privacy_consented") -> list[str]:
    """한 사람의 동의 이벤트 경로(`via`)를 시각순으로 돌려준다. 기본은 필수 동의 이벤트다."""
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(DomainEvent.payload["via"].astext)
            .where(DomainEvent.event_type == event_type,
                   DomainEvent.target_id == UUID(uid))
            .order_by(DomainEvent.occurred_at)
        )
        return list(rows)


async def _withdrawals(uid: str) -> list[dict]:
    """한 사람의 선택 동의 철회 이벤트 페이로드를 시각순으로 돌려준다."""
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(DomainEvent.payload)
            .where(DomainEvent.event_type == "user.optional_privacy_consent_withdrawn",
                   DomainEvent.target_id == UUID(uid))
            .order_by(DomainEvent.occurred_at)
        )
        return list(rows)


async def _seed_optional(uid: str) -> None:
    """선택 동의와 선택 항목이 남아 있는 계정 상태를 만든다. 이전 버전에서 저장한 값이 남은 계정과 같다."""
    async with SessionLocal() as s:
        await s.execute(update(User).where(User.user_id == UUID(uid)).values(
            optional_privacy_consented_at=datetime.now(UTC), phone_number="010-7777-8888",
            birth_date=date(1991, 2, 3), address="광주"))
        await s.commit()


async def _clear_consent(uid: str) -> None:
    """동의 기록이 없는 계정 상태를 만든다. 관리자가 막 만든 계정과 같은 상태다."""
    async with SessionLocal() as s:
        await s.execute(update(User).where(User.user_id == UUID(uid)).values(
            privacy_consented_at=None))
        await s.commit()


async def test_unconsented_user_is_blocked_until_consent(client: AsyncClient, admin_token: str):
    """동의 기록이 없는 계정은 업무 API가 403이고, 동의하면 바로 쓸 수 있는지 확인한다.

    내 정보 조회와 동의 기록, 로그아웃은 동의 전에도 열려 있다. 동의 화면이 이 세 경로를 쓴다.
    """
    uid, token = await make_user(client, admin_token, "gate")
    await _clear_consent(uid)
    hdr = _h(token)

    r = await client.get("/projects", headers=hdr)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "privacy_consent_required"
    r = await client.patch("/users/me", headers=hdr, json={"display_name": "바꿈"})
    assert r.status_code == 403
    me = await _me(client, hdr)
    assert me["user_id"] == uid and me["privacy_consented_at"] is None

    r = await client.post("/users/me/privacy-consent", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["privacy_consented_at"] is not None
    assert (await client.get("/projects", headers=hdr)).status_code == 200

    # 같은 요청이 다시 와도 동의 시각과 이벤트는 그대로다.
    first = r.json()["privacy_consented_at"]
    again = await client.post("/users/me/privacy-consent", headers=hdr)
    assert again.status_code == 200 and again.json()["privacy_consented_at"] == first
    # 계정은 관리자가 만들고 사용자가 동의 화면에서 동의한다. 테스트가 동의를 지운 뒤 다시 동의했다.
    assert await _consent_vias(uid) == ["consent_screen", "consent_screen"]

    assert (await client.post("/auth/logout", headers=SAME_ORIGIN, json={})).status_code == 204


async def test_unconsented_super_admin_is_blocked_too(client: AsyncClient, admin_token: str):
    """최고관리자도 동의 전에는 관리 API를 쓰지 못하는지 확인한다."""
    uid, token = await make_user(client, admin_token, "gate-sa")
    async with SessionLocal() as s:
        await s.execute(update(User).where(User.user_id == UUID(uid)).values(
            global_role="super_admin", privacy_consented_at=None))
        await s.commit()
    hdr = _h(token)
    r = await client.get("/users", headers=hdr)
    assert r.status_code == 403 and r.json()["detail"] == "privacy_consent_required"
    assert (await client.post("/users/me/privacy-consent", headers=hdr)).status_code == 200
    assert (await client.get("/users", headers=hdr)).status_code == 200


async def test_withdrawal_is_refused(client: AsyncClient, admin_token: str):
    """필수 동의 철회 요청이 422이고 두 동의 시각과 개인정보를 그대로 두는지 확인한다.

    동의는 계정을 쓰는 조건이라 철회는 계정 삭제 요청으로 처리한다.
    """
    uid, token = await make_user(client, admin_token, "nowd")
    hdr = _h(token)
    await _seed_optional(uid)
    before = await _me(client, hdr)

    r = await client.patch("/users/me", headers=hdr, json={"privacy_consent": False})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "privacy_consent_withdrawal_unavailable"
    after = await _me(client, hdr)
    assert after["privacy_consented_at"] == before["privacy_consented_at"]
    assert after["optional_privacy_consented_at"] == before["optional_privacy_consented_at"]
    assert after["phone_number"] == "010-7777-8888"


# 선택 동의


async def test_optional_withdrawal_purges_values(client: AsyncClient, admin_token: str):
    """선택 동의를 철회하면 선택 항목을 바로 지우고 시각을 비우며 필수 동의는 그대로인지 확인한다.

    감사 기록에는 지운 필드 이름만 남고 값은 남지 않는다.
    """
    uid, token = await make_user(client, admin_token, "optwd")
    hdr = _h(token)
    await _seed_optional(uid)
    before = await _me(client, hdr)

    r = await client.delete("/users/me/optional-privacy-consent", headers=hdr)
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["optional_privacy_consented_at"] is None
    assert (after["phone_number"], after["birth_date"], after["address"]) == (None, None, None)
    assert after["privacy_consented_at"] == before["privacy_consented_at"]
    async with SessionLocal() as s:
        row = await s.get(User, UUID(uid))
    assert (row.phone_number, row.birth_date, row.address) == (None, None, None)

    events = await _withdrawals(uid)
    assert len(events) == 1
    assert sorted(events[0]["purged"]) == ["address", "birth_date", "phone_number"]
    assert "010-7777-8888" not in str(events[0]) and "광주" not in str(events[0])

    # 동의가 없으면 철회는 아무것도 바꾸지 않는다.
    again = await client.delete("/users/me/optional-privacy-consent", headers=hdr)
    assert again.status_code == 200
    assert len(await _withdrawals(uid)) == 1

    assert (await client.get("/projects", headers=hdr)).status_code == 200


# 이행 마이그레이션

#: 마이그레이션 폴더. 선택 동의 시각을 더하고 옮기는 파일을 이 폴더에서 찾는다.
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic/versions"


async def test_migration_moves_consent_only_for_users_with_values():
    """이행이 선택 항목 값이 있는 사용자에게만 동의 시각을 선택 동의 시각으로 옮기고, 되돌린 뒤 다시 올려도 같은지 확인한다.

    값이 없는 사용자와 필수 동의가 없는 사용자는 비워 둔다. 트랜잭션 안에서 돌리고 되돌려 다른 테스트에 흔적을 남기지 않는다.
    """
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import text

    found = list(_VERSIONS.glob("*_optional_privacy_consent.py"))
    assert len(found) == 1, found
    spec = importlib.util.spec_from_file_location("migration_optional_consent", found[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    at = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    rows = {
        "with_value": (at, "010-1234-5678", None, None),
        "birth_only": (at, None, date(1990, 1, 2), None),
        "no_value": (at, None, None, None),
        "no_consent": (None, None, None, "서울"),
    }
    ids = {k: uuid4() for k in rows}

    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            def _run(c):
                """사용자 넷을 넣고 downgrade·upgrade를 두 번 돌려 옮긴 값을 읽는다."""
                for k, (consented, phone, birth, addr) in rows.items():
                    c.execute(text(
                        "INSERT INTO users (user_id, email, display_name, global_role, approval_status, "
                        "email_verified, privacy_consented_at, phone_number, birth_date, address) "
                        "VALUES (:id, :email, :name, 'user', 'active', true, :at, :phone, :birth, :addr)"),
                        {"id": ids[k], "email": uniq(f"mig-{k}"), "name": k, "at": consented,
                         "phone": phone, "birth": birth, "addr": addr})
                module.op = Operations(MigrationContext.configure(c))
                seen = []
                for _ in range(2):
                    module.downgrade()
                    cols = c.execute(text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'users' "
                        "AND column_name = 'optional_privacy_consented_at'")).scalar()
                    assert cols == 0
                    module.upgrade()
                    seen.append(dict(c.execute(text(
                        "SELECT display_name, optional_privacy_consented_at FROM users "
                        "WHERE user_id = ANY(:ids)"), {"ids": list(ids.values())}).all()))
                return seen

            seen = await conn.run_sync(_run)
        finally:
            await trans.rollback()
    for got in seen:
        assert got == {"with_value": at, "birth_only": at, "no_value": None, "no_consent": None}


async def test_new_optional_consent_and_values_are_not_taken(client: AsyncClient, admin_token: str):
    """선택 동의와 선택 항목을 새로 받지 않는지 확인한다. 프로필 수정은 이름만 바꾸고 선택 항목은 그대로 둔다."""
    uid, token = await make_user(client, admin_token, "optno")
    await _clear_consent(uid)
    hdr = _h(token)
    r = await client.post("/users/me/privacy-consent", headers=hdr,
                          json={"optional_privacy_consent": True})
    assert r.status_code == 200, r.text
    assert r.json()["privacy_consented_at"] is not None
    assert r.json()["optional_privacy_consented_at"] is None
    assert (await client.post("/users/me/optional-privacy-consent", headers=hdr)).status_code == 405

    r = await client.patch("/users/me", headers=hdr, json={
        "display_name": "이름만", "phone_number": "010-5555-6666", "address": "대전",
    })
    assert r.status_code == 200, r.text
    me = await _me(client, hdr)
    assert me["display_name"] == "이름만"
    assert me["phone_number"] is None and me["address"] is None
