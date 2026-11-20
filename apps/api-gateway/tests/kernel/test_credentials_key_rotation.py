"""자격증명 암호화 키 교체 테스트.

키를 바꾸면 이전 키로 된 시크릿을 모두 읽지 못한다. 그래서 새 키(`CREDENTIALS_KEY`)와 이전 키
(`CREDENTIALS_KEY_PREVIOUS`)를 함께 받고, 이전 키 암호문을 새 키로 바꾸는 명령을 둔다. 보장하는 것은 다섯이다.

1. 이전 키 목록에 있는 키로 된 암호문을 복호하고, 새로 쓰는 암호문은 새 키로 만든다.
2. 이전 키 없이 키만 바꾸면 복호가 시크릿 없는 `ValueError`로 실패하고, 재암호화는 그 행을 바꾸지 않고 센다.
3. 드라이런은 건수만 세고 바꾸지 않는다.
4. 적용은 이전 키 행만 새 키로 바꾸고 `updated_at`을 건드리지 않으며, 실행 중 화면에서 바뀐 행을 덮지 않는다.
   다시 적용하면 바꿀 행이 없다.
5. 명령 출력에는 건수만 있고 암호문이나 시크릿이 없다.

테스트 DB의 다른 연결도 이전 키 행으로 세어져 새 키로 바뀐다. 그래서 테스트 끝에 키를 되돌려 다시 적용해
원래 키로 돌려놓는다.
"""
import json
import os
import subprocess
import sys
import uuid

import psycopg
import pytest
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import Connection, DomainEvent
from api_gateway.services import crypto
from sqlalchemy import delete, select
from tests._helpers import default_org_id

_NEW = "new-credentials-key-for-rotation-test-0123456789"
_OTHER = "unrelated-key-that-nobody-configured-0123456789"
_PLAIN = "AKIA-ROTATION-SENTINEL\nSECRET-ROTATION-SENTINEL"


@pytest.fixture
def keys(monkeypatch):
    """설정 키를 바꾸는 함수를 준다. 끝나면 원래 키로 돌리고 캐시를 비운다."""
    original = settings.credentials_key

    def _set(current: str, previous: str = "") -> None:
        """새 키와 이전 키 목록을 바꾸고 캐시를 비운다."""
        monkeypatch.setattr(settings, "credentials_key", current)
        monkeypatch.setattr(settings, "credentials_key_previous", previous)
        crypto._keys.cache_clear()

    yield _set, original
    monkeypatch.setattr(settings, "credentials_key", original)
    monkeypatch.setattr(settings, "credentials_key_previous", "")
    crypto._keys.cache_clear()


def test_previous_key_decrypts_and_new_writes_use_the_new_key(keys):
    """이전 키 목록으로 옛 암호문을 풀고, 새 암호문은 새 키로 만드는지 확인한다."""
    set_keys, original = keys
    old_token = crypto.encrypt(_PLAIN)

    set_keys(_NEW, f" {original} , ")
    assert crypto.decrypt(old_token) == _PLAIN
    assert crypto.key_state(old_token) == "previous"
    rotated = crypto.rotate(old_token)
    assert crypto.key_state(rotated) == "current" and crypto.decrypt(rotated) == _PLAIN
    assert crypto.key_state(crypto.encrypt("x")) == "current"

    # 이전 키를 빼도 새 키 암호문은 그대로 읽힌다.
    set_keys(_NEW)
    assert crypto.decrypt(rotated) == _PLAIN


def test_wrong_key_fails_cleanly_without_echoing_secrets(keys):
    """이전 키 없이 키만 바꾸면 복호와 재암호화가 시크릿·암호문을 싣지 않은 `ValueError`로 실패하는지 확인한다."""
    set_keys, _ = keys
    old_token = crypto.encrypt(_PLAIN)
    set_keys(_NEW)
    assert crypto.key_state(old_token) == "unreadable"
    for call in (crypto.decrypt, crypto.rotate):
        with pytest.raises(ValueError) as e:
            call(old_token)
        text = str(e.value)
        assert "SENTINEL" not in text and old_token not in text and _NEW not in text


async def _add(org: str, token: str) -> uuid.UUID:
    """주어진 암호문으로 연결 행을 넣고 id를 돌려준다."""
    async with SessionLocal() as s:
        conn = Connection(org_id=uuid.UUID(org), name=f"rekey-{uuid.uuid4().hex[:8]}",
                          provider="s3", config={"bucket": "b"}, secret_ciphertext=token)
        s.add(conn)
        await s.commit()
        return conn.id


async def _row(cid: uuid.UUID) -> tuple[str, object]:
    """연결 행의 암호문과 `updated_at`을 읽는다."""
    async with SessionLocal() as s:
        c = await s.get(Connection, cid)
        return c.secret_ciphertext, c.updated_at


async def _restore(set_keys, original: str) -> None:
    """테스트가 새 키로 바꾼 행을 원래 키로 되돌린다. 다른 테스트가 원래 키로 읽게 한다."""
    set_keys(original, _NEW)
    async with SessionLocal() as s:
        await crypto.rekey_connections(s, apply=True)


async def test_rekey_dry_run_then_apply(keys, monkeypatch):
    """드라이런은 바꾸지 않고, 적용은 이전 키 행만 새 키로 바꾸며 실행 중 바뀐 행은 덮지 않는지 확인한다."""
    set_keys, original = keys
    org = await default_org_id()
    old_a = await _add(org, crypto.encrypt("a\nA"))
    old_b = await _add(org, crypto.encrypt("b\nB"))
    set_keys(_OTHER)
    lost = await _add(org, crypto.encrypt("c\nC"))
    set_keys(_NEW, original)
    ids = (old_a, old_b, lost)
    before = {cid: await _row(cid) for cid in ids}
    try:
        async with SessionLocal() as s:
            dry = await crypto.rekey_connections(s, apply=False)
        assert dry["previous"] >= 2 and dry["unreadable"] >= 1 and dry["rotated"] == 0, dry
        assert {cid: await _row(cid) for cid in ids} == before, "드라이런이 행을 바꿨다"

        # 실행 중에 화면이 old_b의 시크릿을 새 값으로 바꾼 경우를 흉내 낸다. 다른 연결에서 커밋한다.
        replaced = crypto.encrypt("b2\nB2")
        real_rotate = crypto.rotate
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

        def _rotate_racing(token: str) -> str:
            """old_b를 돌릴 차례가 오면 먼저 다른 연결에서 그 행을 바꿔 둔다."""
            if token == before[old_b][0]:
                with psycopg.connect(dsn, autocommit=True) as c:
                    c.execute("UPDATE connections SET secret_ciphertext = %s WHERE id = %s",
                              (replaced, old_b))
            return real_rotate(token)

        monkeypatch.setattr(crypto, "rotate", _rotate_racing)
        async with SessionLocal() as s:
            done = await crypto.rekey_connections(s, apply=True)
        monkeypatch.setattr(crypto, "rotate", real_rotate)
        assert done["rotated"] >= 1 and done["changed_during_run"] == 1, done

        token_a, updated_a = await _row(old_a)
        assert crypto.key_state(token_a) == "current" and crypto.decrypt(token_a) == "a\nA"
        assert updated_a == before[old_a][1], "재암호화가 마지막 수정 시각을 바꿨다"
        assert (await _row(old_b))[0] == replaced, "실행 중 바뀐 값을 덮었다"
        assert (await _row(lost))[0] == before[lost][0], "못 푸는 행을 바꿨다"

        async with SessionLocal() as s:
            ev = (await s.scalars(select(DomainEvent)
                                  .where(DomainEvent.event_type == "system.credentials_rekeyed")
                                  .order_by(DomainEvent.occurred_at.desc()).limit(1))).first()
            assert ev is not None and ev.payload["rotated"] == done["rotated"]
            assert all(isinstance(v, int) for v in ev.payload.values()), "기록에 값이 섞였다"
            again = await crypto.rekey_connections(s, apply=True)
        assert again["previous"] == 0 and again["rotated"] == 0, again
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Connection).where(Connection.id.in_(ids)))
            await s.commit()
        await _restore(set_keys, original)


async def test_failed_apply_leaves_every_row_unchanged(keys, monkeypatch):
    """적용 중 한 행에서 실패하면 앞서 바꾼 행까지 하나도 커밋되지 않는지 확인한다."""
    set_keys, original = keys
    org = await default_org_id()
    ids = (await _add(org, crypto.encrypt("a\nA")), await _add(org, crypto.encrypt("b\nB")))
    set_keys(_NEW, original)
    before = {cid: await _row(cid) for cid in ids}
    calls = 0
    real_rotate = crypto.rotate

    def _rotate_then_fail(token: str) -> str:
        """두 번째 행에서 실패한다."""
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("중간 실패")
        return real_rotate(token)

    monkeypatch.setattr(crypto, "rotate", _rotate_then_fail)
    try:
        async with SessionLocal() as s:
            with pytest.raises(RuntimeError):
                await crypto.rekey_connections(s, apply=True)
        monkeypatch.setattr(crypto, "rotate", real_rotate)
        assert {cid: await _row(cid) for cid in ids} == before, "실패한 적용이 일부 행을 남겼다"
    finally:
        monkeypatch.setattr(crypto, "rotate", real_rotate)
        async with SessionLocal() as s:
            await s.execute(delete(Connection).where(Connection.id.in_(ids)))
            await s.commit()
        await _restore(set_keys, original)


def _command(*args: str, **env_over: str) -> subprocess.CompletedProcess:
    """운영 명령을 별도 프로세스로 실행한다."""
    env = {**os.environ, "ENVIRONMENT": "test", "REBORN_ENV_FILE": "",
           "DATABASE_URL": settings.database_url, **env_over}
    return subprocess.run([sys.executable, "-m", "api_gateway.cli.rotate_credentials_key", *args],
                          env=env, capture_output=True, text=True, timeout=120, check=False)


def test_command_prints_counts_only():
    """명령이 드라이런으로 건수 JSON 한 줄만 내고 암호문을 출력하지 않는지 확인한다."""
    r = _command()
    assert r.returncode in (0, 1), r.stderr
    out = json.loads(r.stdout.strip())
    assert out["mode"] == "dry-run" and out["rotated"] == 0
    assert set(out) == {"mode", "total", "current", "previous", "unreadable", "rotated",
                        "changed_during_run"}
    assert "gAAAA" not in r.stdout + r.stderr, "암호문이 출력됐다"


async def test_command_exits_one_when_a_row_is_unreadable(keys):
    """어느 키로도 못 푸는 행이 있으면 종료 코드 1로 알리고 키 값을 출력하지 않는지 확인한다."""
    set_keys, original = keys
    org = await default_org_id()
    set_keys(_OTHER)
    lost = await _add(org, crypto.encrypt(_PLAIN))
    set_keys(original)
    try:
        r = _command("--apply", CREDENTIALS_KEY=original)
        assert r.returncode == 1, r.stdout + r.stderr
        out = json.loads(r.stdout.strip())
        assert out["mode"] == "apply" and out["unreadable"] >= 1
        assert _OTHER not in r.stdout + r.stderr and "SENTINEL" not in r.stdout + r.stderr
        assert (await _row(lost))[0] is not None
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Connection).where(Connection.id == lost))
            await s.commit()
