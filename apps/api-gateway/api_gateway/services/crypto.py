"""연결 자격증명의 대칭 암호화. 운영자가 넣은 클라우드 시크릿을 DB에 암호화해 저장한다.

시크릿은 쓰기 전용이다. 화면에는 값 대신 ●●●●와 마지막 갱신 시각만 표시하고,
어댑터와 진단이 실제로 쓸 때만 복호한다.

`cryptography`의 Fernet을 쓴다. Fernet은 인증 암호화(AES-CBC와 HMAC)라서 변조된 암호문은
복호에 실패한다. IV와 MAC도 Fernet이 조립한다.

DB에는 암호문만 두므로 DB만 유출되면 시크릿은 드러나지 않는다. 이 보호는 복호 키가
DB 밖(env `CREDENTIALS_KEY`)에 있을 때만 성립한다.
`config._enforce_prod_secrets`가 prod에서 기본값을 막는다.

Fernet은 32바이트 urlsafe-base64 키를 요구한다. 운영자가 이 형식을 맞추기 어려워 임의 문자열을 받아
`sha256`으로 32바이트를 만든다. 키의 엔트로피는 입력 문자열이 정하고, 이 변환은 형식만 맞춘다.

키를 바꿀 때는 새 키(`CREDENTIALS_KEY`)와 이전 키(`CREDENTIALS_KEY_PREVIOUS`)를 함께 받는다.
암호화는 늘 새 키로 하고, 복호는 새 키부터 이전 키 순서로 시도한다(MultiFernet).
이전 키를 받지 않고 키만 바꾸면 저장된 시크릿을 모두 읽지 못한다.
이전 키로 된 암호문은 `rekey_connections`가 새 키 암호문으로 바꾼다.
운영 명령은 `api_gateway.cli.rotate_credentials_key`다.
절차는 `infra/deploy/README.md`의 자격증명 암호화 키 교체 절에 있다.
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import Connection
from api_gateway.services import audit

#: 암호문을 푸는 키의 종류.
#: `current`는 새 키, `previous`는 이전 키, `unreadable`은 어느 키로도 못 푸는 경우다.
KeyState = Literal["current", "previous", "unreadable"]


def _fernet_of(raw: str) -> Fernet:
    """임의 문자열 키를 sha256으로 32바이트로 만들고 urlsafe-base64로 인코딩해 Fernet을 만든다."""
    digest = hashlib.sha256(raw.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


@lru_cache(maxsize=1)
def _keys() -> tuple[Fernet, MultiFernet]:
    """새 키의 Fernet과 새 키·이전 키를 함께 쓰는 MultiFernet을 만든다.

    프로세스마다 한 번만 만든다.
    """
    primary = _fernet_of(settings.credentials_key)
    previous = [_fernet_of(k) for k in settings.credentials_key_previous_list
                if k != settings.credentials_key]
    return primary, MultiFernet([primary, *previous])


def encrypt(plaintext: str) -> str:
    """시크릿을 새 키로 암호화해 암호문 문자열로 바꾼다. DB에는 이 문자열을 그대로 저장한다.

    빈 문자열도 암호화한다. 시크릿 없음은 컬럼 `NULL`로 표현하며 빈 시크릿과 다르다.
    """
    return _keys()[0].encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """암호문을 시크릿으로 복호한다. 새 키로 먼저 풀고, 못 풀면 이전 키를 차례로 쓴다.

    Raises:
        ValueError: 어느 키로도 풀지 못한 경우(키가 바뀌었거나 암호문이 변조된 경우).
            빈 값을 돌려주면 외부 인증 실패로만 보여 원인을 찾을 수 없으므로 예외를 던진다.
    """
    try:
        return _keys()[1].decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("자격증명 복호 실패 — CREDENTIALS_KEY가 바뀌었을 수 있습니다") from e


def key_state(token: str) -> KeyState:
    """암호문이 새 키로 되어 있는지, 이전 키로 되어 있는지, 어느 키로도 못 푸는지 판정한다.

    재암호화 명령이 바꿀 행을 세는 기준이다. 평문은 돌려주지 않는다.
    """
    primary, multi = _keys()
    try:
        primary.decrypt(token.encode())
        return "current"
    except InvalidToken:
        pass
    try:
        multi.decrypt(token.encode())
        return "previous"
    except InvalidToken:
        return "unreadable"


def rotate(token: str) -> str:
    """이전 키로 된 암호문을 새 키 암호문으로 바꾼다. 새 키 암호문을 넣어도 새 키로 다시 암호화한다.

    Raises:
        ValueError: 어느 키로도 풀지 못한 경우.
    """
    try:
        return _keys()[1].rotate(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError(
            "자격증명 복호 실패 — CREDENTIALS_KEY_PREVIOUS에 이전 키가 있는지 확인합니다") from e


async def rekey_connections(db: AsyncSession, *, apply: bool) -> dict[str, int]:
    """연결 시크릿 가운데 이전 키로 된 암호문을 새 키 암호문으로 바꾸고 건수를 돌려준다.

    값은 돌려주지 않고 로그에도 남기지 않는다.
    키 교체 명령(`api_gateway.cli.rotate_credentials_key`)이 부른다.
    한 행씩 비교 후 갱신(`secret_ciphertext`가 읽은 값과 같을 때만)으로 쓴다.
    그 사이 화면에서 시크릿을 바꾼 행은 이미 새 키로 암호화돼 있으므로 덮지 않고
    `changed_during_run`으로 센다.
    모든 갱신과 감사 기록을 한 트랜잭션으로 커밋하므로, 중간에 실패하면 아무 행도 바뀌지 않는다.
    다시 실행하면 새 키 행을 건너뛰므로 같은 결과가 된다.
    `updated_at`은 그대로 둔다. 화면의 마지막 수정 시각이 시크릿을 바꾼 시각으로 보이지 않게 한다.

    Args:
        db: 세션. `apply`가 참이면 이 함수가 커밋한다.
        apply: 거짓이면 세기만 하고 바꾸지 않는다.

    Returns:
        `total`(시크릿이 있는 연결) · `current`(새 키) · `previous`(이전 키) ·
        `unreadable`(어느 키로도 못 품) · `rotated`(이번에 바꾼 행) ·
        `changed_during_run`(실행 중 화면에서 바뀐 행).
    """
    rows = (await db.execute(
        select(Connection.id, Connection.secret_ciphertext)
        .where(Connection.secret_ciphertext.is_not(None))
    )).all()
    counts = {"total": len(rows), "current": 0, "previous": 0, "unreadable": 0,
              "rotated": 0, "changed_during_run": 0}
    for conn_id, token in rows:
        state = key_state(token)
        counts[state] += 1
        if state != "previous" or not apply:
            continue
        result = await db.execute(
            update(Connection)
            .where(Connection.id == conn_id, Connection.secret_ciphertext == token)
            .values(secret_ciphertext=rotate(token), updated_at=Connection.updated_at)
            .execution_options(synchronize_session=False)
        )
        counts["rotated" if result.rowcount == 1 else "changed_during_run"] += 1
    if apply:
        audit.emit(db, event_type="system.credentials_rekeyed", target_kind="connection",
                   payload=dict(counts))
        await db.commit()
    return counts
