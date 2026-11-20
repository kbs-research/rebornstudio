"""최초 설정. "이 배포는 아직 초기화되지 않았다"는 상태를 정의한다.

배포 직후에는 로그인할 수 있는 계정이 없는 것이 정상이다.
그래서 계정 없이 기동하고, 서버에 접근할 수 있는 사람이 첫 관리자를 만든다.
저장소에 적힌 비밀번호로 관리자를 심으면 인터넷에 열린 서버에 그 계정이 그대로 남는다.

초기화 여부 판정은 이 모듈 한 곳에 있다.
`활성 super_admin이 하나도 없으면 미초기화`다. 별도의 "초기화됨" 플래그를 두지 않는다.
플래그는 실제 상태와 어긋날 수 있지만 이 정의는 어긋날 수 없다.
정지(`suspended`)된 계정은 세지 않는다. 그래서 개발용 시드 계정을 지우지 않고 정지해도
외래 키를 끊지 않고 미초기화 상태가 된다(마이그레이션 0057).

첫 관리자를 아무나 만들 수 있으면 안 된다.
초기화 전 화면이 공개 IP에 열려 있으면 먼저 찾은 사람이 관리자가 된다.
그래서 셋업 토큰을 요구한다. api가 기동할 때 미초기화 상태면 토큰을 만들어 로그에 찍는다.
운영자는 그 토큰을 서버에서 읽어 와야 하므로 서버에 접근할 수 있는 사람만 초기화할 수 있다.

토큰은 Redis에 둔다. 프로세스가 여럿이어도 같은 값을 본다.
한 번 발급한 토큰은 초기화가 끝날 때까지 바뀌지 않는다.
발급이 멱등하지 않으면 로그에 찍힌 값이 조회 한 번에 무효가 된다.
"""
from __future__ import annotations

import logging
import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import User

log = logging.getLogger(__name__)

_TOKEN_KEY = "reborn:setup:token"  # 셋업 토큰의 Redis 키


async def is_uninitialized(db: AsyncSession) -> bool:
    """활성 super_admin이 하나도 없으면 True. 초기화 여부는 이 함수만 판정한다."""
    n = await db.scalar(
        select(func.count()).select_from(User).where(
            User.global_role == "super_admin",
            User.approval_status == "active",
        )
    )
    return not n


def issue_token() -> str | None:
    """셋업 토큰을 얻는다. 없을 때만 새로 만든다(멱등). 로그에 찍는 것은 호출자가 한다.

    부를 때마다 새로 발급하면 로그에 찍힌 값이 조회 한 번에 무효가 된다.
    발급은 부작용이므로 조회에 딸려 있으면 안 된다.

    Redis가 없으면(로컬 인메모리 대체) None을 돌려준다. 그 환경은 셋업 토큰을 요구하지 않으므로
    개발자가 자기 노트북에서 토큰을 찾을 필요가 없다.
    """
    from api_gateway import redis_infra

    client = redis_infra.get_redis()
    if not hasattr(client, "set"):
        return None
    try:
        current = client.get(_TOKEN_KEY)
        if current:
            return current.decode() if isinstance(current, bytes) else current
        token = secrets.token_urlsafe(24)
        # nx=True: 레플리카 둘이 동시에 기동해도 먼저 쓴 쪽의 값이 남는다.
        client.set(_TOKEN_KEY, token, nx=True)
        current = client.get(_TOKEN_KEY)
        return current.decode() if isinstance(current, bytes) else current
    except Exception as e:  # noqa: BLE001 — 토큰 발급 실패가 기동을 막지 않는다
        # 경고가 아니라 오류로 남긴다.
        # 실 Redis에서 토큰이 없으면 셋업이 막힌다(`token_state`가 통과시키지 않는다).
        # 운영자는 로그에서 이 줄을 찾아 Redis를 확인해야 한다.
        log.error("셋업 토큰 발급 실패: %s", type(e).__name__)
        return None


def token_required() -> bool:
    """셋업 토큰을 요구할 수 있는 환경이면 True. 조회만 한다.

    `/setup/status`가 이 함수를 쓴다. 발급 함수를 쓰면 조회가 토큰을 만들어 버린다.
    """
    from api_gateway import redis_infra

    return hasattr(redis_infra.get_redis(), "get")


class SetupTokenUnavailableError(RuntimeError):
    """토큰 저장소(Redis)를 읽지 못한 오류. 검사를 통과시키지 않고 라우트가 503을 돌려준다."""


def token_state(candidate: str) -> str:
    """제출된 토큰을 판정한다. `"ok"`·`"wrong"`·`"missing"` 중 하나를 돌려준다.

    비교는 상수 시간으로 한다. 문자열 `==`는 앞부분이 얼마나 맞는지를 응답 시간으로 드러낸다.
    이 토큰은 super_admin을 만드는 열쇠라서 그 정도의 누출도 허용하지 않는다.

    인메모리 대체 환경(local/test)만 검사를 생략하고 `"ok"`를 돌려준다.
    그 환경은 토큰을 발급할 수 없으므로 토큰을 요구하면 초기화 자체가 불가능하다.
    실 Redis에서는 확인에 실패하면 통과시키지 않는다.
    Redis 오류나 빈 토큰을 생략 분기로 보내면 첫 배포와 Redis 장애가 겹칠 때 토큰 없이
    첫 super_admin을 만들 수 있다.

    `"missing"`은 두 경우다. 초기화가 끝나 토큰을 지웠거나(정상) 기동 때 발급이 실패한 경우다.
    어느 쪽인지는 DB로 알 수 있고 라우트가 구분한다.
    이 함수는 통과시키지 않는 데까지만 한다.

    Raises:
        SetupTokenUnavailableError: Redis를 읽지 못한 경우. 일시 오류다.
    """
    from api_gateway import redis_infra

    client = redis_infra.get_redis()
    if not hasattr(client, "get"):
        return "ok"
    try:
        current = client.get(_TOKEN_KEY)
    except Exception as e:  # noqa: BLE001 — 어떤 오류든 통과가 아니라 "지금은 모른다"다
        log.error("셋업 토큰 조회 실패 — 검사를 통과시키지 않는다: %s", type(e).__name__)
        raise SetupTokenUnavailableError from e
    if not current:
        return "missing"
    if isinstance(current, bytes):
        current = current.decode()
    return "ok" if secrets.compare_digest(current, candidate or "") else "wrong"


def reissue_token() -> None:
    """미초기화인데 토큰이 없을 때 새로 발급해 서버 로그에만 찍는다(기동 로그와 같은 형식).

    Raises:
        SetupTokenUnavailableError: 발급하지 못한 경우. 이번 요청은 셋업을 진행할 수 없다.
    """
    fresh = issue_token()
    if fresh is None:
        raise SetupTokenUnavailableError
    log.warning("셋업 토큰을 다시 발급했습니다 — 셋업 토큰: %s", fresh, extra={"no_db": True})


def clear_token() -> None:
    """초기화가 끝나면 토큰을 지운다. 용도가 끝난 토큰을 남겨 두지 않는다."""
    import contextlib

    from api_gateway import redis_infra

    client = redis_infra.get_redis()
    if not hasattr(client, "delete"):
        return
    with contextlib.suppress(Exception):   # 토큰 정리 실패가 초기화를 되돌리지 않는다
        client.delete(_TOKEN_KEY)


async def complete(
    db: AsyncSession, *, email: str, display_name: str, password: str, privacy_consent: bool,
) -> str:
    """첫 super_admin을 만든다. advisory lock으로 요청을 직렬화한다.

    `WHERE NOT EXISTS`만으로는 동시 생성을 막을 수 없다.
    READ COMMITTED에서 두 트랜잭션은 각자 자기 스냅샷을 보므로 서로의 미커밋 INSERT를
    보지 못한다. 그래서 둘 다 조건을 통과해 둘 다 관리자가 된다.
    읽고 판단하고 쓰는 과정을 SQL 한 문장으로 옮겨도 경쟁 조건은 남는다.
    이 경쟁은 동시 요청에서만 드러나므로 단독 실행 테스트로는 잡히지 않는다.

    `pg_advisory_xact_lock`은 트랜잭션 범위라서 COMMIT·ROLLBACK 때 자동으로 풀린다.
    `joblock`이 쓰는 세션 범위 락(`pg_try_advisory_lock`)은 전용 커넥션이 필요하다.
    `AsyncSession`은 `commit()`마다 커넥션을 반환하므로 같은 세션으로 잡은 세션 범위 락은
    풀리지 않고 남는다. 트랜잭션 범위 락에는 이 문제가 없다.

    advisory lock은 인스턴스 전역이라 락 키를 DB 범위로 준다.
    고정 상수 하나만 쓰면 같은 인스턴스의 다른 DB(개발·테스트)끼리 초기화를 서로 막는다.

    첫 super_admin도 다른 계정과 같이 개인정보 수집·이용 동의가 있어야 만든다.

    Returns:
        생성된 user_id.

    Raises:
        HTTPException: 개인정보 수집·이용 동의가 없는 경우(422 `privacy_consent_required`),
            비밀번호가 정책에 맞지 않는 경우(422), 이미 초기화된 경우(409),
            이메일이 이미 쓰이고 있는 경우(409).
    """
    import anyio
    from fastapi import HTTPException
    from fastapi import status as st
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from api_gateway.security import hash_password, validate_password_policy
    from api_gateway.services.auth import consent
    from api_gateway.services.auth.service import _PWD_POLICY_MSG

    consent.require_given(privacy_consent)

    # 가장 권한이 큰 계정이 가장 약한 비밀번호를 갖지 않게 한다.
    # 가입·변경·재설정과 같은 정책을 쓰고, 락을 잡기 전에 거른다.
    if not validate_password_policy(password):
        raise HTTPException(st.HTTP_422_UNPROCESSABLE_ENTITY, _PWD_POLICY_MSG)

    # 해시는 락을 잡기 전에 스레드에서 만든다. 락 안에서 수백 ms를 쓰지 않고 루프도 막지 않는다.
    password_hash = await anyio.to_thread.run_sync(hash_password, password)

    # 이 문장부터 커밋까지가 임계 구역이다. 락을 먼저 잡고 나서 판정한다.
    await db.execute(text(
        "SELECT pg_advisory_xact_lock(hashtext('reborn:setup'), hashtext(current_database()))"
    ))
    try:
        row = (await db.execute(
            text("""
                INSERT INTO users (
                    email, display_name, global_role,
                    approval_status, password_hash, email_verified, privacy_consented_at
                )
                SELECT :email, :name, 'super_admin', 'active', :hash, true, now()
                 WHERE NOT EXISTS (
                    SELECT 1 FROM users
                     WHERE global_role = 'super_admin'
                       AND approval_status = 'active'
                 )
                RETURNING user_id
            """),
            {"email": email, "name": display_name, "hash": password_hash},
        )).first()
    except IntegrityError as e:
        await db.rollback()
        # 대부분 정지된 시드 계정과 같은 이메일을 고른 경우라서 응답에서 그 사실을 알려 준다.
        raise HTTPException(
            st.HTTP_409_CONFLICT, "정지된 계정을 포함해 이미 사용 중인 이메일입니다") from e

    if row is None:
        await db.rollback()
        raise HTTPException(st.HTTP_409_CONFLICT, "이미 최초 설정을 마쳤습니다")

    consent.record(db, row[0], via="setup")
    await db.commit()
    clear_token()
    log.warning("최초 설정 완료 — super_admin 생성: %s", email)
    return str(row[0])
