"""감사 로그와 통합 이벤트 기록 도우미.

서비스는 commit 직전에 `emit`을 불러 `DomainEvent`를 같은 트랜잭션에 추가한다(outbox 규약).
그래서 상태 변경과 이벤트가 원자적으로 함께 남는다. commit은 호출자가 한다.

`kind="integration"`은 다른 도메인이 구독할 사실에만 쓴다.
기록(audit)과 달리 계약이므로 이름이나 payload를 바꾸면 구독자가 깨진다.
이벤트 목록은 `services/README.md`의 이벤트 카탈로그에 있다.
"""
from collections.abc import Sequence
from uuid import UUID

from fastapi import Request
from reborn_contracts import DomainEventDTO
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import DomainEvent, User

_UA_MAX = 200


def client_ip(request: Request | None) -> str | None:
    """요청을 보낸 클라이언트 주소를 돌려준다. 로그인 한도와 감사 출처가 함께 쓰는 기준이다.

    프록시 해석은 서버 입구에서 한 번만 한다. uvicorn의 `--proxy-headers`는
    `FORWARDED_ALLOW_IPS`(신뢰하는 프록시 목록)에서 온 요청일 때만 `X-Forwarded-For`를 읽고
    `request.client`를 원래 클라이언트로 바꾼다(`infra/docker/api.Dockerfile`).
    이 함수는 그 결과만 읽고 헤더를 다시 파싱하지 않는다.

    모든 요청의 XFF 첫 홉을 믿으면 누구든 헤더 하나로 감사 출처를 꾸밀 수 있다.
    XFF를 무시하면 프록시 뒤의 모든 사용자가 nginx 주소 하나로 세어진다.
    그러면 출근 시각에 로그인 한도(429)에 걸린다.
    그래서 로그인 한도와 감사 출처가 모두 신뢰하는 프록시만 반영한 이 주소를 쓴다.

    Returns:
        클라이언트 주소. 요청이나 클라이언트 정보가 없으면 None.
    """
    if request is None or request.client is None:
        return None
    return request.client.host or None


def origin(request: Request | None) -> dict[str, str | None]:
    """인증 이벤트에 붙일 접속 출처(IP·User-Agent)를 뽑는다.

    접속 출처는 개인정보지만 보안 추적에 필요해서 담는다.
    출처가 없으면 로그인 이벤트로 누가 어디서 접속했는지 추적할 수 없다.
    담은 출처는 보존 정책의 대상이다. 요청 본문과 자격증명은 담지 않는다.

    주소는 `client_ip`에서 가져온다. 신뢰하는 프록시가 넘긴 값만 반영된 주소다.

    Returns:
        `ip`·`user_agent` 키의 사전. 얻지 못한 값은 None.
    """
    if request is None:
        return {"ip": None, "user_agent": None}
    ua = request.headers.get("user-agent")
    return {"ip": client_ip(request), "user_agent": ua[:_UA_MAX] if ua else None}


def emit(
    db: AsyncSession,
    *,
    event_type: str,
    target_kind: str,
    target_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    project_id: UUID | None = None,
    payload: dict | None = None,
    kind: str = "audit",
) -> None:
    """DomainEvent를 현재 트랜잭션에 추가한다. commit은 호출자가 한다."""
    db.add(DomainEvent(
        event_type=event_type,
        target_kind=target_kind,
        target_id=target_id,
        actor_user_id=actor_user_id,
        project_id=project_id,
        payload=payload or {},
        kind=kind,
    ))


#: 기본 조회에서 빼는 세션 자동 갱신 이벤트 유형.
#: `auth.refreshed`는 토큰 rotation마다 남는다. 화면은 만료 2분 전마다 토큰을 갱신하므로
#: (`useSessionKeepalive`) 활동 중인 사용자 1명당 한 시간에 한 행이 쌓인다.
#: 100명이 8시간 일하면 하루 800행의 `인증 세션 갱신`이 쌓여 사람이 한 작업이 그 사이에 묻힌다.
#: 세션 유지 기간은 보안 조사에 쓰이므로 행은 지우지 않는다. 필터를 켜면 다시 보인다.
#: 진단(`diagnostics.py`)도 같은 이유로 상태가 바뀐 경우만 이벤트로 남긴다.
SESSION_REFRESH_EVENT_TYPES = ("auth.refreshed",)


def exclude_session_refresh(stmt):
    """세션 갱신 행을 조회에서 뺀다.

    사용자 활동 이력이 쓴다. 이유는 `SESSION_REFRESH_EVENT_TYPES` 주석에 있다.

    Args:
        stmt: `DomainEvent`를 고르는 select 문.

    Returns:
        같은 문에 제외 조건을 더한 select 문.
    """
    return stmt.where(DomainEvent.event_type.not_in(SESSION_REFRESH_EVENT_TYPES))



async def with_actor_names(
    db: AsyncSession, events: Sequence[DomainEvent],
) -> list[DomainEventDTO]:
    """이벤트를 DTO로 바꾸고 행위자 이름을 서버에서 붙인다.

    행위자 이름은 이벤트에 딸린 사실이므로 조회하는 사용자의 프로젝트 소속과 무관하게
    같아야 한다. 행위자에는 멤버가 아닌 채로 운영 액션을 하는 `super_admin`과 프로젝트에서
    나간 멤버도 있다. 화면이 프로젝트 멤버 목록에서 이름을 찾으면 이 행들이 "알 수 없음"으로
    표시된다.

    id를 모아 한 번에 조회한다. 행마다 조회하면 목록마다 N+1 쿼리가 된다.
    행위자가 없는 시스템 이벤트는 조회하지 않는다.

    Args:
        events: 직렬화할 `DomainEvent` 목록. 순서는 그대로 유지한다.

    Returns:
        `actor_name`·`actor_email`을 채운 DTO 목록.
        찾을 수 없는 사용자는 값을 만들지 않고 `None`으로 둔다.
    """
    who = await actor_names(db, [e.actor_user_id for e in events])
    out: list[DomainEventDTO] = []
    for e in events:
        dto = DomainEventDTO.model_validate(e, from_attributes=True)
        name, email = who.get(e.actor_user_id, (None, None))
        out.append(dto.model_copy(update={"actor_name": name, "actor_email": email}))
    return out


async def actor_names(db: AsyncSession, ids) -> dict:
    """행위자 id를 `(이름, 이메일)`로 바꾼다. 한 번의 쿼리로 조회한다.

    `with_actor_names`(사용자 활동 이력)가 쓴다.

    Args:
        ids: 행위자 id 목록. `None`은 건너뛰고 중복은 합친다.

    Returns:
        찾은 사용자만 담은 사전. 찾지 못한 사용자는 키가 빠진다. 호출부가 `None`으로 처리한다.
    """
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = await db.execute(
        select(User.user_id, User.display_name, User.email).where(User.user_id.in_(wanted))
    )
    return {uid: (dn, em) for uid, dn, em in rows}
