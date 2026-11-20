"""연결 자격증명 관리. CRUD와 실제 접속 점검을 한다.

시크릿은 쓰기 전용이다. 받은 시크릿은 `crypto.encrypt`로 암호화해 저장한다.
응답 DTO에는 시크릿을 싣지 않는다(`_to_dto`에 access/secret 키가 없다).
화면은 `has_secret`으로 설정 여부만 표시한다.
요청이 나가는 곳(`ROUTING_KEYS`)을 바꾸는 수정은 시크릿을 다시 받는다(`secret_required`).
주소만 바꿔 저장된 시크릿을 다른 서버로 보내는 경로를 막는다.

연결을 만들거나 고치면 저장을 커밋한 뒤 같은 요청에서 클라우드에 실제로 접속해 본다(`check_saved`).
접속까지 검증해 두면 등록은 되는데 분석만 실패하는 연결이 생기지 않고, 점검이 정상인 새 연결을
프로젝트에서 바로 고를 수 있다. 점검이 실패해도 저장은 되돌리지 않고 실패를 행에 남긴다.

연결 자격증명을 실제로 쓰는 곳은 접속 점검(`check_saved`·`recheck_all`), 캡션 품질검수의
모델 호출(`caption_qc.runner`), 그리고 프로젝트가 고른
저장소(`ncp`)·분석(`maiu`) 연결이다. 저장소 클라이언트(`media.storage`)와 MI 클라이언트가
`platform_credentials`를 거쳐 연결 id로 읽는다.
프로젝트가 연결을 고르는 규칙은 `project_storage`·`project_stack`에 있다.

점검 결과의 `detail`·`hint`는 화면에 그대로 나가므로 화면 용어를 쓴다.
`버킷`은 `저장소`, `시크릿 키`는 `비밀 키`, 프로바이더는 `서비스`로 쓴다.
`엔드포인트`는 클라우드 콘솔의 입력란 이름이라 그대로 둔다.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts.connection import (
    ConnectionChoiceDTO,
    ConnectionCreateRequest,
    ConnectionDTO,
    ConnectionHealthSummary,
    ConnectionProblem,
    ConnectionPurpose,
    ConnectionSaved,
    ConnectionTestResult,
    ConnectionUpdateRequest,
)

# `update`는 이 모듈의 서비스 함수 이름과 겹치므로 SQL 쪽에 별칭을 준다.
from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import Connection, Project, User
from api_gateway.services import (
    audit,
    authz,
    crypto,
    platform_credentials,
)
from api_gateway.services.caption_qc import adapters as qc_adapters
from api_gateway.services.caption_qc import settings as qc_settings
from api_gateway.services.maiu import client as maiu_client

# 예외를 한국어 사유로 바꿀 때 플랫폼 진단과 같은 사전을 쓴다.
# 그래야 개요 카드와 연결 목록이 같은 실패를 같은 사유로 표시한다.
# `ops.diagnostics`를 읽는 교차 import는 도메인 독립 계약의 허용 목록에 있다.
from api_gateway.services.ops.diagnostics import (
    CREDENTIAL_REJECTED,
    cloud_error_code,
    exception_reason,
)
from api_gateway.services.stacks import project_stack, project_storage

log = logging.getLogger(__name__)

#: 사용 중 거절 응답에 이름을 싣는 프로젝트 수의 상한. 나머지는 수만 싣는다.
IN_USE_NAMES_MAX = 5


class InUseError(HTTPException):
    """프로젝트가 쓰고 있어 연결의 삭제·수정을 거절하는 409. 막는 프로젝트를 응답에 싣는다.

    응답은 `{"detail": 코드, "projects": [이름…], "projects_more": 나머지 수}`이다(`main.py`).
    `detail`은 다른 오류와 같은 코드 문자열이라 화면의 오류 사전이 그대로 읽는다.
    이름이 있어야 무엇을 먼저 바꿀지 화면이 안내할 수 있다.
    """

    def __init__(self, code: str, projects: list[str]) -> None:
        """코드와 막는 프로젝트 이름을 받는다. 이름은 정렬해 앞의 `IN_USE_NAMES_MAX`개만 싣는다."""
        super().__init__(status.HTTP_409_CONFLICT, code)
        names = sorted(projects)
        self.projects = names[:IN_USE_NAMES_MAX]
        self.projects_more = len(names) - len(self.projects)


def _to_dto(c: Connection, used_by: dict[str, list[str]] | None = None) -> ConnectionDTO:
    """ORM을 DTO로 바꾼다. 시크릿은 설정 여부만 내보내고 값은 내보내지 않는다.

    `used_by`는 목록 응답에서만 채운다. 생성·수정 화면은 이 값을 쓰지 않는다.
    세려면 쿼리가 하나 더 필요하므로 두 응답에서는 비워 둔다.
    """
    return ConnectionDTO(
        id=c.id, name=c.name, provider=c.provider,
        config=c.config or {},
        has_secret=c.secret_ciphertext is not None,
        secret_set_at=c.secret_set_at, updated_at=c.updated_at, updated_by=c.updated_by,
        last_checked_at=c.last_checked_at, last_status=c.last_status,
        last_detail=c.last_detail,
        used_by=sorted((used_by or {}).get(c.name, [])),
    )


async def list_for_org(db: AsyncSession, org_id: UUID) -> list[ConnectionDTO]:
    """조직의 연결 목록을 이름 순으로 돌려준다. 시크릿 값은 싣지 않는다.

    각 연결을 쓰는 프로젝트(`used_by`)도 함께 싣는다. 서버가 시크릿을 돌려주지 않아 지운 연결은
    되살릴 수 없으므로, 삭제 판단 근거인 사용처를 목록에 함께 둔다.
    사용처는 조직 전체를 한 번에 모은다(`_users_of`). 연결마다 세면 N+1 쿼리가 된다.
    """
    rows = (await db.scalars(
        select(Connection).where(Connection.org_id == org_id).order_by(Connection.name)
    )).all()
    used_by = await _users_of(db, org_id)   # 한 번만 부른다. 행마다 부르면 N+1이 된다
    return [_to_dto(c, used_by=used_by) for c in rows]


async def choices_for_project(
    db: AsyncSession, project_id: UUID, user: User, purpose: ConnectionPurpose | None = None,
) -> list[ConnectionChoiceDTO]:
    """프로젝트 설정에서 고를 수 있는 연결(그 프로젝트 조직의 연결)을 이름 순으로 돌려준다.

    연결 관리 목록은 최고관리자 전용이라, 캡션 품질검수 모델 연결을 고르는 프로젝트 관리자는
    이 함수로 선택지를 받는다. 이름과 상태만 싣고 설정값·점검 사유·시크릿은 싣지 않는다.

    `purpose`를 주면 그 목적에 쓸 수 있는 연결만 돌려준다. 판정에는 저장 검사
    (`caption_qc.settings.check_connection_name`)와 같은 함수를 쓴다. 저장이 거절될 연결을
    선택지에 넣지 않는다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            그 프로젝트의 관리자가 아닌 경우(403 not_project_admin 등).
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)
    rows = (await db.scalars(
        select(Connection).where(Connection.org_id == project.org_id).order_by(Connection.name)
    )).all()
    out = [
        ConnectionChoiceDTO(
            name=c.name, provider=c.provider,
            has_secret=c.secret_ciphertext is not None, last_status=c.last_status,
            adapter_keys=qc_adapters.adapter_keys_for(c.provider),
        )
        for c in rows
    ]
    if purpose == "caption_qc":
        return [c for c in out if c.adapter_keys]
    return out


#: 문제 연결을 정렬하는 상태별 우선순위. 실패가 가장 앞이다.
_ATTENTION_RANK = {"fail": 0, "warn": 1, "unchecked": 2}


async def health_summary(db: AsyncSession) -> ConnectionHealthSummary:
    """모든 연결의 마지막 점검 상태를 센다.

    플랫폼 개요의 "자격증명 연결" 줄이 쓴다. 상태는 행의 `last_status`를 그대로 쓴다.
    개요가 따로 판정하면 연결 목록과 다른 상태가 표시된다.

    상태는 쓰는 곳이 있는 연결만 센다. 쓰는 곳이 없는 연결은 `unused`로만 세어, 어떤 기능도
    쓰지 않는 연결의 점검 실패가 개요의 실패 수에 들어가지 않게 한다. 쓰는 곳의 판정은
    연결 목록의 `used_by`와 같은 참조(`_connection_refs`)를 쓴다.
    쓰는 곳이 있는 실패·주의 연결은 `problems`에 이름·원인과 함께 싣는다.
    """
    rows = (await db.scalars(select(Connection).order_by(Connection.name))).all()
    users = await _users_by_pair(db)
    out = ConnectionHealthSummary()
    for conn in rows:
        out.total += 1
        used_by = users.get((conn.org_id, conn.name)) or []
        if not used_by:
            out.unused += 1
            continue
        st = conn.last_status
        key = st if st in ("ok", "warn", "fail") else "unchecked"
        setattr(out, key, getattr(out, key) + 1)
        if key in ("fail", "warn"):
            out.problems.append(ConnectionProblem(
                id=conn.id, name=conn.name,
                provider=conn.provider, status=key, detail=conn.last_detail,
                checked_at=conn.last_checked_at, used_by=sorted(used_by),
            ))
    # 실패를 주의보다 먼저 둔다. `sort`는 안정 정렬이라 같은 상태 안에서는 이름 순이 남는다.
    out.problems.sort(key=lambda p: _ATTENTION_RANK[p.status])
    return out


def _connection_refs():
    """프로젝트 설정에서 연결 이름을 꺼내는 SQL 식을 만든다. 목록의 `used_by`와 개요 집계가 쓴다.

    이름으로 가리키는 참조는 캡션 품질검수의 모델 연결 하나다. 분석 대상의 MI 연결은 id로
    가리키므로 `project_stack.users_by_connection`이 따로 센다. 이름 참조가 늘면 이 목록에 더한다.
    빠뜨리면 그 설정이 쓰는 연결이 `사용처 없음`으로 보여 삭제될 수 있다.
    """
    # `->>`로 꺼낸다. `jsonb_path_query_first`는 두 번째 인자를 `jsonpath`로 캐스팅해야 해서
    # 바인드 파라미터로는 쓸 수 없다(`function … (jsonb, character varying) does not exist`).
    return (
        Project.settings[qc_settings.SETTINGS_KEY]["connection_name"].astext,
    )


async def _users_by_pair(db: AsyncSession) -> dict[tuple[UUID, str], list[str]]:
    """전 조직의 `(조직, 연결 이름)`별 사용 프로젝트 이름을 한 번에 모은다. 개요 집계가 쓴다.

    캡션 품질검수 실행기가 `(프로젝트의 조직, 연결 이름)`으로 연결을 찾으므로 같은 쌍을 키로 쓴다.
    판정에는 목록의 `used_by`와 같은 식(`_connection_refs`)을 쓴다.
    한 프로젝트가 여러 설정에서 같은 연결을 골라도 한 번만 센다.
    """
    out: dict[tuple[UUID, str], list[str]] = {}
    rows: list = []
    for ref in _connection_refs():
        rows += (await db.execute(
            select(Project.org_id, Project.name, ref).where(ref.is_not(None))
        )).all()
    # 분석 대상은 이름이 아니라 연결 id로 가리킨다. 같은 키로 세려고 연결 이름으로 바꿔 받는다.
    rows += await project_stack.users_by_connection(db)
    for org_id, proj_name, conn_name in rows:
        names = out.setdefault((org_id, conn_name), []) if conn_name else None
        if names is not None and proj_name not in names:
            names.append(proj_name)
    return out


async def _users_of(db: AsyncSession, org_id: UUID) -> dict[str, list[str]]:
    """조직의 연결별 사용 프로젝트(`{연결 이름: [쓰는 프로젝트 이름…]}`)를 한 번에 모은다.

    참조가 이름이라 오타가 있으면 어느 연결에도 잡히지 않는다. 이 함수는 이름이 일치하는
    참조만 센다. 오타는 설정 화면이 알린다. `_connection_refs`의 참조와 분석 대상의
    MI 연결을 모두 센다.
    """
    out: dict[str, list[str]] = {}
    pairs: list = []
    for ref in _connection_refs():
        pairs += (await db.execute(
            select(Project.name, ref).where(Project.org_id == org_id, ref.is_not(None))
        )).all()
    pairs += [(p, c) for _, p, c in await project_stack.users_by_connection(db, org_id)]
    for proj_name, conn_name in pairs:
        names = out.setdefault(conn_name, []) if conn_name else None
        if names is not None and proj_name not in names:
            names.append(proj_name)
    return out


#: 연결 설정에서 서버가 요청을 보내는 주소 키. 저장소 엔드포인트와 API·모델 서버 주소다.
_ADDRESS_KEYS = ("base_url", "endpoint")

#: 연결 종류별로 요청이 나가는 곳을 정하는 설정 키.
#: 저장소는 엔드포인트와 리전이다. 엔드포인트가 비면 리전이 호스트를 정한다.
#: Bedrock은 리전, 모델 공급자와 MI는 API 주소다.
#: 화면도 같은 표를 쓴다. 두 런타임의 테스트가 `packages/reborn-contracts/connection-address.json`을
#: 함께 읽어 답을 맞춘다.
ROUTING_KEYS: dict[str, tuple[str, ...]] = {
    "s3": ("endpoint", "region"),
    "ncp": ("endpoint", "region"),
    "gcs": ("endpoint", "region"),
    "maiu": ("base_url",),
    "openai": ("base_url",),
    "anthropic": ("base_url",),
    "local-llm": ("base_url",),
    "bedrock": ("region",),
}
#: 표에 없는 연결 종류가 쓰는 키. 요청이 나가는 곳을 정할 수 있는 키를 모두 본다.
_ALL_ROUTING_KEYS = ("base_url", "endpoint", "region")

#: 클라우드 인스턴스 메타데이터 서비스의 호스트 이름. 점이 있는 이름만 적는다.
#: 점 없는 이름(`metadata`·`instance-data`)은 `_internal_host`가 한꺼번에 막는다.
_METADATA_HOSTS = frozenset({
    "metadata.google.internal", "metadata.goog", "instance-data.ec2.internal",
})

#: IPv4 주소를 담는 NAT64 대역. 안에 든 IPv4 주소로 다시 판정한다.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _host_ip(name: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """호스트 이름이 IP 주소 표기면 그 주소를, 아니면 None을 돌려준다.

    `2130706433`·`0x7f.1`·`127.1`처럼 점 네 개가 아닌 IPv4 표기도 HTTP 클라이언트는 루프백으로
    푼다. 그래서 숫자와 16진 표기로만 된 이름은 `inet_aton`으로 한 번 더 읽는다.
    """
    try:
        return ipaddress.ip_address(name)
    except ValueError:
        pass
    if re.fullmatch(r"[0-9a-fx.]+", name) and re.search(r"\d", name):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(name))
        except OSError:
            return None
    return None


def _internal_host(name: str) -> bool:
    """서버 내부나 클라우드 메타데이터를 가리키는 호스트면 True다.

    막는 것은 루프백·링크 로컬(169.254.0.0/16, fe80::/10)·미지정(0.0.0.0)·멀티캐스트·예약 대역 IP,
    메타데이터 호스트 이름, `localhost`와 그 하위 이름, 점 없는 이름이다. 점 없는 이름은 compose
    네트워크의 서비스 이름(postgres·redis·api·web·worker 등)과 컨테이너 이름으로 풀린다.
    사설 대역(10/8·172.16/12·192.168/16·fc00::/7)은 받는다.
    사내 모델 서버가 사설 주소에 있을 수 있다.
    """
    if name in _METADATA_HOSTS or name == "localhost" or name.endswith(".localhost"):
        return True
    ip = _host_ip(name)
    if ip is None:
        return "." not in name
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return (ip.is_loopback or ip.is_link_local or ip.is_unspecified
            or ip.is_multicast or ip.is_reserved)


def _allowlisted(name: str) -> bool:
    """호스트가 `CONNECTION_HOST_ALLOWLIST`에 정확히 같은 값으로 있으면 True다."""
    allowed = {h.strip().lower().strip("[]") for h in settings.connection_host_allowlist.split(",")}
    return name in allowed - {""}


def address_problem(raw: str) -> str | None:
    """연결 주소 하나를 판정하고 쓸 수 없으면 사유 코드를, 쓸 수 있으면 None을 돌려준다.

    http(s)만 받고 주소에 사용자 이름이나 비밀번호를 넣지 못하게 한다. 내부 호스트는
    `_internal_host`가 정하고 `CONNECTION_HOST_ALLOWLIST`에 적은 호스트만 예외로 받는다.
    호스트 이름을 DNS로 풀지 않으므로 공용 이름이 내부 주소로 풀리는 경우는 막지 못한다.

    Returns:
        사유 코드(`connection_url_bad_scheme`·`connection_url_userinfo`·
        `connection_url_blocked`)나 None.
    """
    try:
        parts = urlsplit(raw.strip())
        host = parts.hostname
    except ValueError:
        return "connection_url_bad_scheme"
    if parts.scheme.lower() not in ("http", "https") or not host:
        return "connection_url_bad_scheme"
    if parts.username is not None or parts.password is not None:
        return "connection_url_userinfo"
    name = host.rstrip(".").lower()
    if _internal_host(name) and not _allowlisted(name):
        return "connection_url_blocked"
    return None


def _check_addresses(config: dict) -> None:
    """연결 설정의 주소 키를 모두 판정하고 하나라도 쓸 수 없으면 422를 던진다.

    주소 키가 없거나 빈 값이면 넘어간다. 주소 없이 만들고 나중에 채우는 연결과 공식 주소를
    기본값으로 쓰는 연결이 있다.

    Raises:
        HTTPException: http(s)가 아니거나 호스트가 없는 경우(422 `connection_url_bad_scheme`),
            주소에 사용자 이름이나 비밀번호가 있는 경우(422 `connection_url_userinfo`),
            내부·메타데이터 호스트인 경우(422 `connection_url_blocked`).
    """
    for key in _ADDRESS_KEYS:
        raw = config.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        problem = address_problem(str(raw))
        if problem is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)


def routing_keys(provider: str) -> tuple[str, ...]:
    """연결 종류의 요청이 나가는 곳을 정하는 설정 키를 돌려준다."""
    return ROUTING_KEYS.get(provider, _ALL_ROUTING_KEYS)


def _routing_value(key: str, value: object) -> str:
    """주소 비교에 쓰는 값을 만든다. 빈 값과 `None`은 같고, 주소 끝의 슬래시는 무시한다."""
    text = "" if value is None else str(value).strip()
    return text.rstrip("/") if key in _ADDRESS_KEYS else text


def routing_changes(provider: str, before: dict | None, after: dict | None) -> list[str]:
    """두 설정 사이에서 값이 바뀐 주소 키를 돌려준다. 같은 값을 다시 보낸 키는 넣지 않는다."""
    old, new = before or {}, after or {}
    return [k for k in routing_keys(provider)
            if _routing_value(k, old.get(k)) != _routing_value(k, new.get(k))]


def secret_required(
    provider: str, before: dict | None, after: dict | None, *,
    has_secret: bool, secret_sent: bool, clear_secret: bool,
) -> bool:
    """주소를 바꾸는 수정이 시크릿을 다시 받아야 하면 참이다.

    수정은 시크릿을 보내지 않으면 저장된 값을 둔다. 그래서 주소만 바꾸면 다음 점검이 저장된
    시크릿을 새 주소로 보낸다. 저장된 시크릿이 있는 연결의 주소를 바꾸려면 같은 요청에 시크릿을
    넣거나 시크릿을 지워야 한다.
    """
    if not has_secret or secret_sent or clear_secret:
        return False
    return bool(routing_changes(provider, before, after))


def _check_bedrock_keys(
    provider: str, access_key: str | None, secret_key: str | None, *, has_stored: bool
) -> None:
    """Bedrock 연결이 저장 뒤에 액세스 키와 비밀 키를 둘 다 갖는지 확인한다.

    키 없는 Bedrock 연결은 boto3 기본 자격증명 체인으로 넘어간다. 앱 호스트에 AWS 환경변수나
    인스턴스 역할이 있으면 그 서버 권한이 연결 설정 하나로 쓰인다. 그래서 다른 서비스와 달리
    키 없이 먼저 등록하는 흐름을 받지 않는다.

    Args:
        provider: 연결의 서비스.
        access_key: 이번 요청에 보낸 액세스 키. 보내지 않았으면 `None`.
        secret_key: 이번 요청에 보낸 비밀 키. 보내지 않았으면 `None`.
        has_stored: 이번 요청 뒤에도 저장된 시크릿이 남으면 참.

    Raises:
        HTTPException: Bedrock 연결에 두 키가 모두 있지 않은 경우(422 bedrock_keys_required).
    """
    if provider != "bedrock":
        return
    if access_key is None and secret_key is None:
        ok = has_stored
    else:
        ok = bool((access_key or "").strip()) and bool((secret_key or "").strip())
    if not ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bedrock_keys_required")


def _config_changes(provider: str, before: dict | None, after: dict | None) -> dict:
    """연결 수정 감사 기록에 남길 설정 변경을 만든다. 바뀐 키 이름과 주소·버킷 키의 전후 값이다.

    주소를 바꾸면 저장된 자격증명이 새 주소로 나간다. 그래서 주소와 리전의 전후 값을 기록에 남긴다.
    버킷과 허용 버킷 목록은 프로젝트 관리자가 고를 수 있는 범위를 정하므로 전후 값을 남긴다.
    그 밖의 키는 이름만 남긴다. 설정에는 시크릿을 두지 않지만, 공급자마다 키가 달라
    값이 민감한지 키 이름으로 가를 수 없다.
    """
    old, new = before or {}, after or {}
    changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    out: dict = {"config_keys_changed": changed}
    address_keys = set(_ADDRESS_KEYS) | set(routing_keys(provider))
    addresses = {
        k: {"before": old.get(k), "after": new.get(k)} for k in changed if k in address_keys
    }
    if addresses:
        out["address_changes"] = addresses
    buckets = {
        k: {"before": old.get(k), "after": new.get(k)}
        for k in changed if k in project_storage.BUCKET_SCOPE_KEYS
    }
    if buckets:
        out["bucket_changes"] = buckets
    return out


async def create(
    db: AsyncSession, org_id: UUID, payload: ConnectionCreateRequest, actor: User
) -> ConnectionSaved:
    """연결을 만들고 바로 점검한다. 시크릿이 있으면 암호화해 저장한다.

    시크릿 없이 만들고 나중에 채울 수도 있다. 그 연결의 점검은 접속하지 않고 `warn`이다.
    Bedrock은 예외로 두 키가 모두 있어야 만든다(`_check_bedrock_keys`).
    점검 결과는 저장과 별개다. 점검이 실패해도 연결은 만들어지고 실패가 행에 남는다(`check_saved`).

    Raises:
        HTTPException: 같은 조직에 같은 이름이 있는 경우(409 name_exists),
            주소가 규칙에 맞지 않는 경우(422 `connection_url_*`, `_check_addresses`),
            허용 버킷 목록이 버킷 이름 목록이 아닌 경우(422 `connection_allowed_buckets_invalid`),
            Bedrock 연결에 액세스 키와 비밀 키가 모두 있지 않은 경우(422 `bedrock_keys_required`).
    """
    from datetime import UTC, datetime

    _check_bedrock_keys(payload.provider, payload.access_key, payload.secret_key, has_stored=False)
    config = payload.config or {}
    _check_addresses(config)
    config = project_storage.clean_bucket_scope(config)
    secret = _pack_secret(payload.access_key, payload.secret_key)
    conn = Connection(
        org_id=org_id, name=payload.name, provider=payload.provider,
        config=config,
        secret_ciphertext=crypto.encrypt(secret) if secret is not None else None,
        secret_set_at=datetime.now(UTC) if secret is not None else None,
        updated_by=actor.user_id,
    )
    db.add(conn)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "name_exists") from e
    audit.emit(db, event_type="connection.created", target_kind="connection",
               target_id=conn.id, actor_user_id=actor.user_id,
               payload={"provider": conn.provider, "name": conn.name})
    await db.commit()
    # 저장소·분석 연결이면 이 프로세스가 새 연결을 바로 쓰도록 다시 읽는다.
    # 다른 프로세스는 갱신 주기에 읽는다.
    if conn.provider in (platform_credentials.STORAGE_PROVIDER,
                         platform_credentials.ANALYSIS_PROVIDER):
        await platform_credentials.refresh(db)
    check = await check_saved(db, conn)
    return ConnectionSaved(**_to_dto(conn).model_dump(), check=check)


async def update(
    db: AsyncSession, conn_id: UUID, payload: ConnectionUpdateRequest, actor: User
) -> ConnectionSaved:
    """연결을 수정하고 바로 점검한다. `None`인 필드는 바꾸지 않는다.

    시크릿을 보내면 그 값으로 바꾸고, `clear_secret`이 참이면 지우고, 보내지 않으면 그대로 둔다.
    요청이 나가는 곳(`routing_keys`)을 바꾸는 수정은 시크릿을 같은 요청에 다시 받는다
    (`secret_required`). 같은 값을 다시 보낸 키는 바뀌지 않은 것으로 본다.
    프로젝트가 쓰는 버킷이 허용 범위에서 빠지는 수정은 거절한다.
    그 프로젝트의 등록·탐색이 멈추기 때문이다(`project_storage.users_losing_bucket`).
    감사 기록에는 바뀐 설정 키, 주소의 전후 값, 시크릿 교체 여부를 남긴다(`_config_changes`).
    저장을 커밋한 뒤 점검한다(`check_saved`). 점검이 실패해도 수정은 남고 실패가 행에 남는다.

    이름은 바꿀 수 없다. 프로젝트 설정(품질검수)이 연결을 이름으로 가리키므로
    이름을 바꾸면 그 설정의 참조가 끊긴다.

    Raises:
        HTTPException: 연결이 없는 경우(404 not_found), 같은 이름이 있는 경우(409 name_exists),
            다른 이름을 보낸 경우(422 connection_name_immutable),
            주소가 규칙에 맞지 않는 경우(422 `connection_url_*`, `_check_addresses`),
            저장된 시크릿이 있는데 시크릿 없이 주소를 바꾸는 경우
            (422 `secret_required_for_address_change`),
            허용 버킷 목록이 버킷 이름 목록이 아닌 경우(422 `connection_allowed_buckets_invalid`),
            수정 뒤 Bedrock 연결에 액세스 키와 비밀 키가 모두 있지 않은 경우
            (422 `bedrock_keys_required`).
        InUseError: 프로젝트가 쓰는 버킷이 허용 범위에서 빠지는 경우(409 allowed_bucket_in_use).
    """
    from datetime import UTC, datetime

    conn = await db.get(Connection, conn_id)
    if not conn:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    before_config = dict(conn.config or {})

    if payload.name is not None and payload.name != conn.name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "connection_name_immutable")
    secret = _pack_secret(payload.access_key, payload.secret_key)
    _check_bedrock_keys(
        conn.provider,
        None if payload.clear_secret else payload.access_key,
        None if payload.clear_secret else payload.secret_key,
        has_stored=conn.secret_ciphertext is not None and not payload.clear_secret,
    )
    if payload.config is not None:
        _check_addresses(payload.config)
        if secret_required(conn.provider, before_config, payload.config,
                           has_secret=conn.secret_ciphertext is not None,
                           secret_sent=secret is not None, clear_secret=payload.clear_secret):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "secret_required_for_address_change")
        new_config = project_storage.clean_bucket_scope(payload.config)
        losing = await project_storage.users_losing_bucket(db, conn, new_config)
        if losing:
            raise InUseError("allowed_bucket_in_use", losing)
        conn.config = new_config

    if payload.clear_secret:
        conn.secret_ciphertext = None
        conn.secret_set_at = None
    elif secret is not None:
        conn.secret_ciphertext = crypto.encrypt(secret)
        conn.secret_set_at = datetime.now(UTC)
    conn.updated_by = actor.user_id
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "name_exists") from e
    # UPDATE에는 RETURNING이 붙지 않아 서버 `onupdate` 값(updated_at)이 로드되지 않는다.
    # 그래서 미리 다시 읽는다. 읽지 않으면 아래 `_to_dto`가 그 속성을 읽을 때 지연 IO가 일어나
    # `MissingGreenlet`으로 실패한다. INSERT에는 RETURNING이 붙어 다시 읽을 필요가 없다.
    await db.refresh(conn)
    audit.emit(db, event_type="connection.updated", target_kind="connection",
               target_id=conn.id, actor_user_id=actor.user_id,
               payload={"cleared_secret": payload.clear_secret,
                        "secret_replaced": secret is not None and not payload.clear_secret,
                        **_config_changes(conn.provider, before_config, conn.config)})
    await db.commit()
    # 프로젝트가 고른 연결의 키를 바꿨으면 이 프로세스는 바로 새 키를 쓴다.
    # 다른 프로세스는 갱신 주기 안에 읽는다.
    await platform_credentials.refresh(db)
    check = await check_saved(db, conn)
    return ConnectionSaved(**_to_dto(conn).model_dump(), check=check)


async def delete(db: AsyncSession, conn_id: UUID, actor: User) -> None:
    """연결을 삭제한다. 프로젝트가 쓰는 연결이면 409를 던진다.

    쓰는 연결을 지우면 그 프로젝트의 캡션 품질검수·비식별화·분석 요청이 나중에 실패한다.
    그래서 쓰는 곳을 먼저 다른 연결로 바꾸게 한다. 판정에는 목록과 같은 `_users_of`를 쓰고,
    저장소·분석 대상으로 고른 프로젝트도 센다.

    Raises:
        HTTPException: 연결이 없는 경우(404 not_found).
        InUseError: 프로젝트가 쓰는 경우(409 connection_in_use).
    """
    conn = await db.get(Connection, conn_id)
    if not conn:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    users = (await _users_of(db, conn.org_id)).get(conn.name)
    if users:
        raise InUseError("connection_in_use", users)
    await db.delete(conn)
    audit.emit(db, event_type="connection.deleted", target_kind="connection",
               target_id=conn_id, actor_user_id=actor.user_id, payload={"name": conn.name})
    await db.commit()


async def check_saved(db: AsyncSession, conn: Connection) -> ConnectionTestResult:
    """저장을 마친 연결에 실제로 접속해 점검하고 결과를 행에 남긴다.

    연결 생성·수정이 커밋한 뒤에 부른다. 실패해도 저장을 되돌리지 않는다. 되돌리면 자격증명 없이
    먼저 등록하는 흐름과 일시적인 네트워크 오류에서 입력이 사라진다. 실패한 연결은 고칠 때까지
    프로젝트가 고를 수 없다.
    자격증명이 없으면 접속하지 않고 `warn`을 돌려준다.
    키 없는 Bedrock 연결은 접속하지 않고 `fail`이다.
    정상과 정상 아님 사이의 전이는 주기 점검과 같이 `connection.health_changed`로 남는다.
    """
    r = await probe_one(conn)
    await record_result(db, conn, r)
    await db.commit()
    # 기록의 UPDATE가 `updated_at`을 식으로 쓰므로 그 속성이 만료된다. 응답을 만들 때 지연 IO로
    # `MissingGreenlet`이 나지 않게 행을 다시 읽어 둔다.
    await db.refresh(conn)
    return r


async def probe_one(conn: Connection) -> ConnectionTestResult:
    """자격증명으로 실제 접속만 시도하고 결과는 기록하지 않는다.

    주기 점검은 이전 상태와 비교해 전이를 기록하므로 접속과 기록을 나눈다.
    한 번에 기록하면 비교 대상이 방금 쓴 값이 되어 전이를 잡을 수 없다.
    """
    # 로컬 모델은 키 없이 동작하는 배포(사내 vLLM)가 흔해 목록에서 뺀다.
    # Bedrock도 목록에 두지 않는다. 키 없는 Bedrock 연결은 설정이 덜 끝난 상태가 아니라 쓰지 않는
    # 상태라서 `_probe_bedrock`이 접속하지 않고 `fail`을 돌려준다.
    _needs_secret = ("s3", "ncp", "maiu", "openai", "anthropic")
    if conn.secret_ciphertext is None and conn.provider in _needs_secret:
        # 자격증명 없이 등록된 연결은 설정이 덜 끝난 상태라 `warn`으로 둔다.
        # 문구는 화면 라벨과 같은 용어로 쓴다. `access/secret`은 화면에 없는 용어다.
        return ConnectionTestResult(
            status="warn", detail="자격증명이 아직 없습니다",
            hint="자격증명을 입력하면 실제 접속을 확인할 수 있습니다.")
    try:
        secret = crypto.decrypt(conn.secret_ciphertext) if conn.secret_ciphertext else ""
    except ValueError:
        return ConnectionTestResult(
            status="fail", detail="저장된 자격증명을 풀지 못했습니다",
            hint=("서버의 자격증명 암호화 키가 바뀌었을 수 있습니다. "
                  "연결을 편집해 자격증명을 다시 입력해주세요."))
    return await _probe(conn, secret)


# 내부 함수.


def _pack_secret(access_key: str | None, secret_key: str | None) -> str | None:
    """access·secret 두 값을 저장용 문자열 하나로 합친다. 둘 다 없으면 `None`을 돌려준다.

    자격증명에는 개행이 없으므로 개행으로 잇는다. 프로바이더에 따라 한쪽만 쓰기도 한다
    (GCS HMAC은 access·secret 둘, MI는 secret 하나).
    """
    if access_key is None and secret_key is None:
        return None
    return f"{access_key or ''}\n{secret_key or ''}"


def _unpack_secret(packed: str) -> tuple[str, str]:
    """저장 문자열을 `(access, secret)`으로 나눈다."""
    access, _, secret = packed.partition("\n")
    return access, secret


async def _probe(conn: Connection, secret: str) -> ConnectionTestResult:
    """프로바이더별로 실제 접속을 시도한다.

    저장소(s3·ncp)는 버킷에 `head_bucket`을 보낸다. Bedrock과 LLM은 모델 목록을 조회하고
    MI 연결은 워크스페이스 정보를 읽는다. 모두 과금이 없는 조회다.
    접속에 성공하면 그 자격증명이 유효하다고 판정한다. GCS 연결은 접속을 확인할 수 없어
    `warn`을 돌려준다.
    """
    cfg = conn.config or {}
    access, secret_key = _unpack_secret(secret)

    if conn.provider in ("s3", "ncp"):
        import boto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError

        bucket = cfg.get("bucket")
        if not bucket:
            return ConnectionTestResult(status="fail", detail="저장소 이름이 없습니다",
                                        hint="저장소 이름을 입력해주세요.")
        client = boto3.client(
            "s3", endpoint_url=cfg.get("endpoint"), region_name=cfg.get("region"),
            aws_access_key_id=access, aws_secret_access_key=secret_key,
            # NCP는 boto3 기본 체크섬을 거부하므로 체크섬 설정을 실제 클라이언트와 똑같이 맞춘다.
            config=Config(signature_version="s3v4",
                          # 기본값(연결 60초와 재시도)이면 응답 없는 엔드포인트 하나에
                          # 저장 응답과 주기 점검이 몇 분씩 멈춘다. 정상 엔드포인트는
                          # 1초 안에 응답하므로 5초 타임아웃으로 충분하다.
                          connect_timeout=5, read_timeout=5,
                          retries={"max_attempts": 1},
                          request_checksum_calculation="when_required",
                          response_checksum_validation="when_required"),
        )
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError as e:
            # 접속했지만 요청이 거절된 경우다. 원인은 권한이나 버킷 이름이다.
            # 오류 코드는 본문에 싣지 않고 로그에만 남긴다.
            code = cloud_error_code(e) or ""
            log.info("연결 프로브 거절 %s: %s", conn.name, code)
            hint = ("액세스 키와 비밀 키, 권한을 확인해주세요." if code in CREDENTIAL_REJECTED
                    else "저장소 이름과 엔드포인트를 확인해주세요.")
            return ConnectionTestResult(
                status="fail", detail=f"저장소 접속 실패 · {exception_reason(e)}", hint=hint)
        except BotoCoreError as e:
            # 접속하지 못한 경우다(엔드포인트 무응답, DNS, 타임아웃, `EndpointConnectionError` 등).
            # 예외를 올리면 연결 저장이 500으로 실패하고 주기 점검이 결과를 기록하지 못하므로
            # 실패도 결과로 돌려준다. 권한 거절(`ClientError`)과 확인할 곳이 달라 문구를 나눈다.
            # 예외 클래스 이름은 서버 로그에만 남긴다.
            log.info("연결 프로브 실패 %s: %s", conn.name, type(e).__name__)
            reason = exception_reason(e)   # 진단 카드와 같은 사전
            if reason == "자격증명이 설정되지 않았습니다":
                return ConnectionTestResult(status="fail", detail=reason,
                                            hint="액세스 키와 비밀 키를 입력해주세요.")
            return ConnectionTestResult(
                status="fail", detail=reason,
                hint="엔드포인트 주소와 네트워크를 확인해주세요. 자격증명 문제와는 다릅니다.")
        except Exception as e:  # noqa: BLE001
            # 프로브는 어떤 예외도 올리지 않고 결과로 돌려주므로 나머지 예외를 모두 받는다.
            # 예외 값에 자격증명이 담길 수 있어 로그에는 타입 이름만 남기고 `fail`을 돌려준다.
            log.warning("연결 프로브 예외 %s: %s", conn.name, type(e).__name__)
            return ConnectionTestResult(
                status="fail", detail="확인하지 못했습니다",
                hint="엔드포인트, 저장소 이름, 자격증명 값을 차례로 확인해주세요.")
        return ConnectionTestResult(status="ok", detail=f"저장소 {bucket} 접속 확인")

    if conn.provider == "bedrock":
        return await _probe_bedrock(conn, access, secret_key)

    if conn.provider in ("openai", "anthropic", "local-llm"):
        # API 키는 앞 값(access)이다. 캡션 품질검수 실행기가 이 값을 `ModelConfig.api_key`로
        # 넘긴다(`caption_qc.runner`). 점검이 다른 값을 읽으면 점검 결과와 실제 호출이 달라진다.
        if not access and secret_key:
            # 뒤 값에만 키가 있는 연결은 품질검수가 키 없이 호출하므로 실패로 판정한다.
            return ConnectionTestResult(
                status="fail", detail="API 키를 읽지 못했습니다",
                hint="연결을 편집해 API 키를 다시 입력해주세요.")
        return await _probe_llm(conn, access)

    if conn.provider == "gcs":
        # GCS 접속 검증은 지원하지 않으므로 접속하지 않고 `warn`을 돌려준다.
        # 문구에는 현재 지원 범위만 쓴다.
        return ConnectionTestResult(
            status="warn", detail="GCS 접속 검증 미지원",
            hint="저장소 이름과 HMAC 값은 저장되지만 접속을 확인할 수 없습니다.")

    if conn.provider == "maiu":
        return await _probe_maiu(conn, access, secret_key)

    return ConnectionTestResult(status="fail",
                                detail=f"지원하지 않는 서비스입니다 ({conn.provider})")


async def _probe_maiu(conn: Connection, access: str, secret: str) -> ConnectionTestResult:
    """MI 워크스페이스 정보를 읽어 키·주소·워크스페이스가 맞는지 확인한다.

    분석 연동이 꺼진 서버에서는 접속하지 않고 정상으로 돌려준다. 분석 요청도 외부에 나가지 않는다.
    분석 요청과 같은 클라이언트(`NaverMaiuClient`)와 같은 서명을 쓴다. 점검이 다른 방식으로
    접속하면 점검은 통과하는데 분석만 실패하는 연결이 생긴다.
    연결 하나를 여러 프로젝트가 고를 수 있으므로 연동 저장소와 분석 프로젝트는 여기서 판정하지
    않는다. 프로젝트가 분석 대상을 고를 때 그 프로젝트 기준으로 점검한다(`project_stack`).
    """
    from api_gateway.services.maiu.naver_client import NaverMaiuClient

    cfg = conn.config or {}
    workspace = str(cfg.get("workspace_name") or "").strip()
    project_id = str(cfg.get("project_id") or "").strip()
    if not workspace:
        return ConnectionTestResult(status="fail", detail="워크스페이스가 없습니다",
                                    hint="워크스페이스 이름을 입력해주세요.")
    if not settings.maiu_enabled:
        # 분석 연동이 꺼진 서버는 분석 요청을 가짜 클라이언트가 처리한다.
        # 그래서 실제 주소에 접속하지 않는다. 접속하면 개발용 주소에서 실패가 기록된다.
        # 분석 대상은 마지막 점검이 정상인 연결만 고를 수 있어 그 실패가 분석 대상 저장을 막는다.
        return ConnectionTestResult(status="ok", detail=maiu_client.FAKE_MODE_DETAIL,
                                    hint=maiu_client.FAKE_MODE_HINT)
    try:
        # 워크스페이스 조회에는 분석 프로젝트가 필요 없어 분석 프로젝트가 비어 있어도 접속을
        # 확인한다. 클라이언트가 빈 값을 거절하므로 임시 값("-")을 넘긴다.
        client = NaverMaiuClient(
            access_key=access, secret_key=secret,
            base_url=str(cfg.get("base_url") or "").strip() or None,
            workspace=workspace, project_id=project_id or "-", timeout=10.0,
        )
        ws = await client.get_workspace()
    except Exception as e:  # noqa: BLE001
        # 예외 문자열에는 주소가 담길 수 있어 화면에는 사전의 사유만 싣는다.
        log.warning("MI 연결 프로브 실패 %s: %s", conn.name, type(e).__name__)
        reason = exception_reason(e)
        # 클라이언트는 예외 메시지에 `MAIU GET /경로 → 상태코드 …` 형식으로 상태 코드를 싣는다.
        found = re.search(r"→\s*(\d{3})\b", str(e))
        status_code = getattr(e, "status", None) or (int(found.group(1)) if found else None)
        if status_code in (401, 403):
            return ConnectionTestResult(status="fail", detail="인증이 거부됐습니다",
                                        hint="액세스 키와 비밀 키를 확인해주세요.")
        if status_code == 404:
            return ConnectionTestResult(status="fail", detail="워크스페이스를 찾지 못했습니다",
                                        hint="워크스페이스 이름을 확인해주세요.")
        return ConnectionTestResult(status="fail", detail=f"분석 서비스 접속 실패 · {reason}",
                                    hint="API 주소와 네트워크를 확인해주세요.")
    body = (ws or {}).get("result") or ws or {}
    linked = str(body.get("bucketName") or "")
    detail = f"워크스페이스 {workspace} 접속 확인"
    return ConnectionTestResult(
        status="ok", detail=f"{detail} · 연결된 저장소 {linked}" if linked else detail)


async def _probe_llm(conn: Connection, api_key: str) -> ConnectionTestResult:
    """LLM 공급자에 실제로 요청해 모델 목록을 한 번 조회한다.

    목록 조회는 토큰 과금이 없고 키·엔드포인트·네트워크 확인에 충분하므로 생성 호출 대신 쓴다.

    주의: 응답에 키를 담지 않는다. 진단 문구는 사람이 읽고 로그에도 남는다.
    """
    import httpx

    cfg = conn.config or {}
    model = (cfg.get("model") or "").strip()
    if conn.provider == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        base = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/models"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        # 예외 클래스 이름은 내부 구현을 드러내므로 화면에 싣지 않고 로그에만 남긴다.
        log.warning("캡션 검수 연결 프로브 실패 %s: %s", conn.name, type(e).__name__)
        return ConnectionTestResult(
            status="fail", detail="연결하지 못했습니다",
            hint=("엔드포인트 주소와 네트워크를 확인해주세요. "
                  "사내 모델이면 모델 서버가 실행 중인지도 확인합니다."),
        )
    if r.status_code in (401, 403):
        return ConnectionTestResult(status="fail", detail="인증이 거부됐습니다",
                                    hint="API 키를 다시 확인해주세요.")
    if r.status_code >= 400:
        # 상태 코드는 로그에만 남기고 본문에는 뜻만 싣는다.
        log.info("캡션 검수 연결 프로브 응답 %s: %s", conn.name, r.status_code)
        return ConnectionTestResult(
            status="fail", detail="모델 목록을 불러오지 못했습니다",
            hint="엔드포인트가 OpenAI 호환인지 확인해주세요.",
        )
    # 모델 이름이 비어 있으면 접속은 되지만 검수를 돌릴 수 없다.
    # 설정이 덜 끝난 상태라 `warn`을 돌려준다.
    if not model:
        return ConnectionTestResult(status="warn", detail="연결은 되지만 모델이 비어 있습니다",
                                    hint="모델 입력란에 사용할 모델 이름을 입력해주세요.")
    return ConnectionTestResult(status="ok", detail=f"연결을 확인했습니다 (모델 {model})")


async def _probe_bedrock(conn: Connection, access: str, secret: str) -> ConnectionTestResult:
    """Bedrock에 실제로 요청해 모델 목록을 조회한다. 토큰 과금이 없다.

    `bedrock-runtime` 대신 `bedrock`(제어 평면)을 호출해 자격증명·리전·권한을 확인한다.
    리전마다 쓸 수 있는 모델이 달라 모델 가용성도 함께 확인한다.
    """
    import asyncio

    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    cfg = conn.config or {}
    region = (cfg.get("region") or "").strip()
    model = (cfg.get("model") or "").strip()
    if not access or not secret:
        # 키 하나만 있어도 boto3는 기본 자격증명 체인으로 넘어간다. 그 체인은 서버 인스턴스
        # 메타데이터의 역할 자격증명을 읽으므로 접속하지 않는다.
        return ConnectionTestResult(
            status="fail", detail="액세스 키와 비밀 키가 없습니다",
            hint="연결을 편집해 두 키를 입력해주세요. 키 없는 Bedrock 연결은 쓰지 않습니다.")
    if not region:
        return ConnectionTestResult(status="fail", detail="리전이 없습니다",
                                    hint="리전을 입력해주세요. 예: us-east-1")

    def _list() -> list[str]:
        """Bedrock 파운데이션 모델 ID 목록을 조회한다. 동기 함수라 스레드에서 실행한다."""
        client = boto3.client(
            "bedrock", region_name=region,
            # S3 분기와 같은 이유로 타임아웃을 짧게 둔다. 기본값이면 막힌 네트워크에서 연결
            # 저장과 주기 점검이 몇 분씩 멈춘다. 정상이면 목록 조회는 1~2초 안에 응답한다.
            config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}),
            aws_access_key_id=access, aws_secret_access_key=secret,
        )
        r = client.list_foundation_models()
        return [m.get("modelId", "") for m in r.get("modelSummaries", [])]

    try:
        model_ids = await asyncio.to_thread(_list)
    except ClientError as e:
        # 오류 코드(`InvalidSignatureException` 등)는 로그에 남긴다. 화면에는 뜻과 확인할 곳을
        # 싣는다. 서명 거절은 키가 틀렸을 때와 서버 시각이 어긋났을 때 모두 나므로 힌트에
        # 둘 다 쓴다.
        code = cloud_error_code(e) or ""
        log.warning("Bedrock 프로브 거절 %s: %s", conn.name, code)
        if code in ("AccessDenied", "AccessDeniedException"):
            hint = "IAM 권한(bedrock:ListFoundationModels)을 확인해주세요."
        elif code in CREDENTIAL_REJECTED:
            hint = "액세스 키와 비밀 키, 서버 시각을 확인해주세요."
        else:
            hint = "리전 이름을 확인해주세요."
        return ConnectionTestResult(
            status="fail", detail=f"Bedrock 조회 실패 · {exception_reason(e)}", hint=hint)
    except BotoCoreError as e:
        log.warning("Bedrock 프로브 실패 %s: %s", conn.name, type(e).__name__)
        return ConnectionTestResult(status="fail", detail=exception_reason(e),
                                    hint="네트워크와 리전을 확인해주세요.")

    if not model:
        return ConnectionTestResult(status="warn", detail="연결은 되지만 모델이 비어 있습니다",
                                    hint="모델 입력란에 모델 ID를 입력해주세요.")
    # 추론 프로파일(`apac.`·`us.` 접두)은 목록에 그 이름으로 나오지 않는다.
    # 그래서 목록에서 찾지 못한 모델은 `fail` 대신 `warn`으로 둔다.
    if not any(model in mid or mid in model for mid in model_ids):
        return ConnectionTestResult(
            status="warn", detail=f"연결은 되지만 목록에 {model} 모델이 없습니다",
            hint=("추론 프로파일이면 정상입니다. "
                  "아니면 이 리전에서 모델 사용이 활성화됐는지 확인해주세요."),
        )
    return ConnectionTestResult(status="ok", detail=f"Bedrock 연결을 확인했습니다 (모델 {model})")


# 주기 점검. `check_saved()`는 저장할 때만 돌아서, 외부 서비스에서 권한이 바뀐 자격증명은
# 다시 저장하기 전까지 드러나지 않는다. 그래서 연결 행을 주기적으로 점검하고 상태를 행에 남긴다.


async def record_result(
    db: AsyncSession, conn: Connection, result: ConnectionTestResult,
) -> bool:
    """점검 결과를 행에 쓰고 상태가 바뀌었으면 전이를 기록한다. commit은 호출부가 한다.

    전이는 `ok`와 그 밖의 상태 사이의 변화만 센다. `warn`(자격증명 미설정)과 `fail`(버킷 없음)을
    나누면 설정 미완 상태 사이의 변화까지 장애로 기록하게 된다.
    `ops.diagnostics`의 플랫폼 진단과 같은 규칙이다.

    Returns:
        상태가 바뀌었으면 `True`.
    """
    # ORM 변경 대신 명시적 UPDATE로 쓴다. `updated_at`에는 `onupdate=now()`가 걸려 있어
    # 점검 결과만 써도 설정 수정 시각이 바뀐다. 그래서 `updated_at = connections.updated_at`으로
    # 자기 값을 명시해 onupdate보다 우선하게 한다. ORM에서 같은 값을 다시 대입하면 dirty로 잡히지
    # 않아 이 방법을 쓸 수 없다. 만료된 속성을 읽으면 암묵적 IO로 MissingGreenlet이 나므로
    # 이전 상태도 SELECT로 읽는다.
    before = await db.scalar(
        select(Connection.last_status).where(Connection.id == conn.id))
    now = datetime.now(UTC)
    values: dict = {"last_status": result.status, "last_detail": result.detail,
                    "last_checked_at": now, "updated_at": Connection.updated_at}
    await db.execute(sql_update(Connection).where(Connection.id == conn.id).values(**values))
    # 호출부가 방금 쓴 값을 바로 읽을 수 있게 메모리의 객체도 맞춘다. 추가 IO는 없다.
    conn.last_status, conn.last_detail, conn.last_checked_at = result.status, result.detail, now

    was_ok, now_ok = before == "ok", result.status == "ok"
    # 처음 점검하는 행은 비교 대상이 없어 전이로 보지 않는다.
    changed = before is not None and was_ok != now_ok
    if changed:
        audit.emit(
            db,
            event_type="connection.health_changed",
            target_kind="connection",
            target_id=conn.id,
            payload={"name": conn.name, "provider": conn.provider,
                     "before": before, "after": result.status, "detail": result.detail},
            kind="audit",
        )
    return changed


async def recheck_all(db: AsyncSession) -> int:
    """모든 연결에 실제로 접속해 점검하고 결과를 기록한다. 주기 태스크가 호출한다.

    자격증명이 없는 행도 점검한다. 그 행에 `warn`이 남아야 화면이 "아직 점검 전"과
    "점검했지만 미완"을 구분한다. 키 없는 Bedrock 행은 `fail`이라 조직 담당자에게 알림이 간다.

    한 연결의 점검이 예외로 실패하면 그 건만 롤백하고 나머지를 계속 점검한다.

    Returns:
        상태가 바뀐 연결 수. 0은 정상 결과다.
    """
    ids = list((await db.scalars(select(Connection.id))).all())
    changed = 0
    for cid in ids:
        conn = await db.get(Connection, cid)
        if conn is None:  # 점검 중에 삭제된 경우다. 정상 흐름이다
            continue
        try:
            # `check_saved()`가 아니라 `probe_one()`으로 접속만 먼저 한다. 접속이 예외로 끝나면
            # 기록하기 전에 그 건만 롤백하고 건너뛸 수 있다.
            result = await probe_one(conn)
        except Exception as e:  # noqa: BLE001 — 한 건의 실패가 배치를 데려가지 않는다
            log.warning("연결 점검 실패 %s: %s", cid, type(e).__name__)
            await db.rollback()
            continue
        if await record_result(db, conn, result):
            changed += 1
        await db.commit()
    return changed
