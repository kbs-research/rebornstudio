"""등록한 연결(저장소·분석) 자격증명의 프로세스 메모리 캐시.

저장소 클라이언트(`media.storage`)와 MI 클라이언트(`maiu.client`)는 동기 경로에서 자격증명을 읽는다.
동기 경로가 DB를 조회하지 않도록 서비스가 `ncp`·`maiu`인 연결 전부를 프로세스마다 연결 id별로
미리 읽어 둔다.
api는 기동할 때와 주기 루프(`run_forever`)에서 다시 읽는다. worker-io는 태스크를 시작할 때
(`refresh_if_stale`) 다시 읽는다. 연결을 고치거나 프로젝트가 대상을 저장한 프로세스는 바로
다시 읽는다.

자격증명의 출처는 프로젝트가 고른 연결 하나다. 프로젝트 설정의 `stack` 블록에 MI 연결·워크스페이스·
MI 프로젝트(`AnalysisTarget`)와 저장소 연결·버킷(`StorageTarget`)이 있다. 영상과 분석 잡은 만들 때
이 값을 복사해 고정한다. 시스템 기본 연결과 서버 환경 설정의 대체값은 두지 않는다.
대체값이 있으면 아무도 고르지 않은 계정이 원본을 받고 분석을 요청하게 된다.

URI에 담긴 버킷의 객체를 읽는 경로(probe·변환 입력·재생 주소)는 그 버킷을 고른 프로젝트의 연결,
그 버킷에 원본이 있는 영상에 고정 기록한 연결, 그 버킷을 설정에 적은 연결 순으로 찾아 읽는다
(`storage_for_bucket`). 세 곳 모두에 없는 버킷은 읽지 않는다.
고른 연결을 읽지 못하면(삭제·복호 실패) 다른 연결로 넘어가지 않는다.
쓸 값이 없으면 `CredentialsMissingError`를 던진다. api는 이 예외를 409로 바꿔 화면에 사유를 보낸다.

이 모듈은 커널 평면 모듈이라 도메인 패키지를 import하지 않는다. 연결을 고르는 규칙(점검 결과,
버킷 일치, 잠금)은 `services/stacks/project_stack`과 `project_storage`에 있다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Final

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import Connection, Project
from api_gateway.services import crypto

log = logging.getLogger(__name__)

#: 저장소 연결이 될 수 있는 서비스. MI가 NCP 버킷만 읽으므로 NCP 하나다.
STORAGE_PROVIDER: Final = "ncp"
#: 분석 연결이 될 수 있는 서비스.
ANALYSIS_PROVIDER: Final = "maiu"
#: 다른 프로세스가 바꾼 연결을 다시 읽는 주기(초). 키 교체가 늦게 적용되는 최대 시간이다.
REFRESH_SECONDS: Final = 60.0

#: 저장소 연결이 주소를 비웠을 때 쓰는 NCP Object Storage 공용 주소.
DEFAULT_NCP_ENDPOINT: Final = "https://kr.object.ncloudstorage.com"
#: 저장소 연결이 리전을 비웠을 때 쓰는 NCP Object Storage 리전.
DEFAULT_NCP_REGION: Final = "kr-standard"
#: MI 연결이 API 주소를 비웠을 때 쓰는 NCP API Gateway 주소.
DEFAULT_MI_BASE_URL: Final = "https://mi.apigw.ntruss.com"

#: 저장소 자격증명이 없을 때의 오류 코드. 화면 문장은 화면 사전(`error_text.ts`)에 있다.
STORAGE_NOT_CONFIGURED: Final = "storage_not_configured"
#: 분석 자격증명이 없을 때의 오류 코드.
ANALYSIS_NOT_CONFIGURED: Final = "analysis_not_configured"

#: 프로젝트 설정 안에서 프로젝트별 스택 선택을 담는 블록 키.
STACK_KEY: Final = "stack"
#: `stack` 블록 안의 MI 연결 id 키.
STACK_ANALYSIS_CONNECTION: Final = "analysis_connection_id"
#: `stack` 블록 안의 MI 워크스페이스 이름 키.
STACK_MAIU_WORKSPACE: Final = "maiu_workspace"
#: `stack` 블록 안의 MI 프로젝트 id 키.
STACK_MAIU_PROJECT: Final = "maiu_project_id"
#: `stack` 블록 안의 MI 프로젝트 이름 키. MI 프로젝트를 고르거나 목록을 읽을 때 확인한 이름이다.
#: 화면 표시에만 쓰고 라우팅에는 쓰지 않는다. MI 목록을 읽지 못하는 동안에도 이름을 표시하려고 둔다.
STACK_MAIU_PROJECT_NAME: Final = "maiu_project_name"
#: `stack` 블록 안의 저장소 연결 id 키.
STACK_STORAGE_CONNECTION: Final = "storage_connection_id"
#: `stack` 블록 안의 버킷 이름 키.
STACK_BUCKET: Final = "bucket"


class CredentialsMissingError(RuntimeError):
    """저장소나 분석 자격증명을 고른 연결에서 얻지 못한 경우의 예외. `code`는 API 오류 코드다."""

    def __init__(self, code: str) -> None:
        """오류 코드를 예외 메시지와 `code`에 담는다."""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StorageCredentials:
    """저장소 클라이언트를 만드는 값 한 묶음."""

    endpoint: str
    region: str
    #: 읽고 쓸 버킷. 연결에서 읽은 값이면 연결 설정의 버킷이다.
    #: 프로젝트·영상의 선택으로 만든 값이면 그 선택의 버킷이다.
    bucket: str
    access_key: str
    secret_key: str = field(repr=False)
    connection_name: str = ""
    #: 값을 읽은 연결의 id. 클라이언트 캐시의 키다.
    connection_id: str = ""

    @property
    def has_keys(self) -> bool:
        """두 키가 모두 있으면 참."""
        return bool(self.access_key and self.secret_key)

    @property
    def complete(self) -> bool:
        """버킷과 두 키가 모두 있으면 참."""
        return bool(self.bucket) and self.has_keys

    @property
    def client_key(self) -> tuple[str, str, str, str]:
        """boto3 클라이언트를 다시 만들지 판정하는 키. 버킷은 클라이언트와 무관해서 넣지 않는다."""
        return (self.endpoint, self.region, self.access_key, self.secret_key)


@dataclass(frozen=True)
class AnalysisCredentials:
    """MI 클라이언트를 만드는 값 한 묶음."""

    access_key: str
    secret_key: str = field(repr=False)
    base_url: str = DEFAULT_MI_BASE_URL
    workspace: str = ""
    project_id: str = ""
    connection_name: str = ""
    #: 값을 읽은 연결의 id.
    connection_id: str = ""

    @property
    def has_keys(self) -> bool:
        """두 키가 모두 있으면 참."""
        return bool(self.access_key and self.secret_key)

    @property
    def complete(self) -> bool:
        """두 키와 워크스페이스, 분석 프로젝트가 모두 있으면 참."""
        return self.has_keys and bool(self.workspace and self.project_id)

    @property
    def client_key(self) -> tuple[str, str, str, str, str]:
        """MI 클라이언트를 다시 만들지 판정하는 키."""
        return (self.access_key, self.secret_key, self.base_url, self.workspace, self.project_id)


@dataclass(frozen=True)
class _Unreadable:
    """읽지 못한 연결의 표시. 그 연결을 고른 프로젝트가 다른 연결로 넘어가지 않게 남긴다."""

    connection_id: str
    reason: str


@dataclass(frozen=True)
class AnalysisTarget:
    """프로젝트가 고른 MI 연결·워크스페이스·MI 프로젝트 한 묶음.

    세 필드가 모두 있어야 분석 요청에 쓸 수 있다(`complete`). 빈 필드는 기본값으로 채우지 않는다.
    """

    #: MI 연결 id 문자열.
    connection_id: str | None = None
    workspace: str | None = None
    project_id: str | None = None

    @property
    def empty(self) -> bool:
        """세 필드가 모두 비었으면 참."""
        return not (self.connection_id or self.workspace or self.project_id)

    @property
    def complete(self) -> bool:
        """세 필드가 모두 있으면 참."""
        return bool(self.connection_id and self.workspace and self.project_id)

    def as_dict(self) -> dict[str, str | None]:
        """잡 payload와 감사 기록에 싣는 형태의 dict로 바꾼다."""
        return {"connection_id": self.connection_id, "workspace": self.workspace,
                "project_id": self.project_id}


@dataclass(frozen=True)
class StorageTarget:
    """프로젝트가 고른 저장소 연결·버킷 한 묶음. 두 필드가 모두 있어야 쓸 수 있다(`complete`)."""

    #: NCP 연결 id 문자열.
    connection_id: str | None = None
    bucket: str | None = None

    @property
    def empty(self) -> bool:
        """두 필드가 모두 비었으면 참."""
        return not (self.connection_id or self.bucket)

    @property
    def complete(self) -> bool:
        """두 필드가 모두 있으면 참."""
        return bool(self.connection_id and self.bucket)

    def as_dict(self) -> dict[str, str | None]:
        """영상 행의 고정 기록과 감사 기록에 싣는 형태의 dict로 바꾼다."""
        return {"connection_id": self.connection_id, "bucket": self.bucket}


#: 서비스가 `maiu`인 연결 전부를 연결 id 문자열별로 읽은 값. 프로젝트가 고른 연결을 여기서 찾는다.
_analysis_by_id: dict[str, AnalysisCredentials | _Unreadable] = {}
#: 서비스가 `ncp`인 연결 전부를 연결 id 문자열별로 읽은 값. 버킷은 연결 설정의 값이다.
_storage_by_id: dict[str, StorageCredentials | _Unreadable] = {}
#: 프로젝트가 고른 버킷과 그 저장소 연결 id.
_bucket_owner: dict[str, str] = {}
#: 마지막으로 DB에서 읽은 시각(`time.monotonic`). 이 프로세스가 아직 읽지 않았으면 `None`.
_loaded_at: float | None = None
#: 메모리에 없는 저장소 연결을 찾은 적이 있으면 참.
#: 참이면 다음 `refresh_if_stale`이 주기를 기다리지 않고 읽는다.
_missed: bool = False


def _unpack(packed: str) -> tuple[str, str]:
    """연결의 저장 문자열을 `(access, secret)`으로 나눈다. `stacks.connections`와 같은 형식이다."""
    access, _, secret = packed.partition("\n")
    return access, secret


def _storage_from(conn: Connection, access: str, secret: str) -> StorageCredentials:
    """NCP 저장소 연결의 값으로 저장소 값을 만든다. 비운 주소·리전은 NCP 공용 값을 쓴다."""
    cfg = conn.config or {}
    return StorageCredentials(
        endpoint=str(cfg.get("endpoint") or "").strip() or DEFAULT_NCP_ENDPOINT,
        region=str(cfg.get("region") or "").strip() or DEFAULT_NCP_REGION,
        bucket=str(cfg.get("bucket") or "").strip(),
        access_key=access, secret_key=secret,
        connection_name=conn.name, connection_id=str(conn.id),
    )


def _analysis_from(conn: Connection, access: str, secret: str) -> AnalysisCredentials:
    """MI 연결의 값으로 분석 값을 만든다. 비운 API 주소는 NCP 공용 주소를 쓴다.

    워크스페이스·분석 프로젝트는 비워 둔다. 분석 요청은 프로젝트가 고른 값만 쓴다(`analysis_for`).
    """
    cfg = conn.config or {}
    return AnalysisCredentials(
        access_key=access, secret_key=secret,
        base_url=str(cfg.get("base_url") or "").strip() or DEFAULT_MI_BASE_URL,
        connection_name=conn.name, connection_id=str(conn.id),
    )


async def refresh(db: AsyncSession) -> None:
    """등록한 연결과 프로젝트가 고른 버킷을 DB에서 다시 읽어 메모리에 둔다.

    DB를 읽지 못하면 이전 값을 그대로 둔다. DB 순단 한 번에 키가 사라지면 진행 중인 업로드와
    폴링이 모두 실패로 끝난다.
    """
    global _analysis_by_id, _storage_by_id, _bucket_owner, _loaded_at, _missed
    try:
        new_by_id = await _load_all(db, ANALYSIS_PROVIDER, _analysis_from)
        new_storage_by_id = await _load_all(db, STORAGE_PROVIDER, _storage_from)
        new_owners = await _load_bucket_owners(db)
    except Exception as e:  # noqa: BLE001
        log.warning("연결을 읽지 못해 이전 값을 유지한다: %s", type(e).__name__)
        _loaded_at = time.monotonic()
        return
    _analysis_by_id, _storage_by_id, _bucket_owner = new_by_id, new_storage_by_id, new_owners
    _loaded_at = time.monotonic()
    _missed = False


async def _load_all(db: AsyncSession, provider: str, build) -> dict:
    """서비스가 `provider`인 연결 전부를 읽어 연결 id별 값으로 만든다.

    결과는 분석 요청·폴링·업로드 서명이 동기 경로에서 읽는다. 시크릿이 없거나 복호하지 못한
    연결은 `_Unreadable`로 둔다. 그래서 그 연결을 고른 프로젝트는 다른 계정으로 넘어가지 않고
    멈춘다.
    """
    out: dict = {}
    conns = await db.scalars(
        select(Connection).where(Connection.provider == provider)
        .execution_options(populate_existing=True))
    for conn in conns:
        key = str(conn.id)
        if not conn.secret_ciphertext:
            out[key] = _Unreadable(key, "no_secret")
            continue
        try:
            access, secret = _unpack(crypto.decrypt(conn.secret_ciphertext))
        except ValueError:
            out[key] = _Unreadable(key, "decrypt_failed")
            continue
        out[key] = build(conn, access, secret)
    return out


async def _load_bucket_owners(db: AsyncSession) -> dict[str, str]:
    """버킷 이름과 그 버킷을 읽을 저장소 연결 id를 모은다.

    URI의 버킷 이름만 있는 경로가 그 버킷을 고른 연결의 키로 읽게 한다. 다음 두 곳에서 모은다.
    - 프로젝트가 고른 버킷과 그 연결. 같은 버킷을 두 프로젝트가 다른 연결로 고르면 id 순으로 앞선
      연결을 쓴다. 두 연결 모두 저장할 때 그 버킷 점검을 통과했다.
    - 영상 원본 URI의 버킷과 그 영상에 고정 기록한 연결. 프로젝트 버킷이 아닌 곳에서 등록한
      원본은 그 영상의 연결 키로 읽는다. 두 곳에 같은 버킷이 있으면 프로젝트가 고른 연결을 쓴다.
    """
    block = Project.settings[STACK_KEY]
    rows = (await db.execute(
        select(block[STACK_BUCKET].astext, block[STACK_STORAGE_CONNECTION].astext)
        .where(block[STACK_BUCKET].astext.is_not(None),
               block[STACK_STORAGE_CONNECTION].astext.is_not(None))
    )).all()
    sources = (await db.execute(text(
        "SELECT DISTINCT substring(source_uri FROM '^[a-z0-9]+://([^/]+)/'), "
        "metadata->'storage'->>'connection_id' FROM videos "
        "WHERE source_uri ~ '^(s3|ncp)://[^/]+/' "
        "AND coalesce(metadata->'storage'->>'connection_id', '') <> ''"
    ))).all()
    out: dict[str, str] = {}
    for group in (rows, sources):
        for bucket, conn in sorted(group, key=lambda r: (r[0] or "", r[1] or "")):
            name, cid = (bucket or "").strip(), (conn or "").strip()
            if name and cid and name not in out:
                out[name] = cid
    return out


def _clean(raw: object) -> str | None:
    """`stack` 블록의 값 하나를 읽는다. 문자열이 아니거나 비었으면 `None`이다."""
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def analysis_target_of(project_settings: dict | None) -> AnalysisTarget | None:
    """프로젝트 설정의 `stack` 블록에서 MI 선택을 꺼낸다. 고르지 않았으면 `None`이다.

    설정 최상위의 `maiu_project_id` 키는 읽지 않는다. 분석 요청이 쓰는 값은 `stack` 블록에만 있고,
    최상위 키를 읽으면 요청이 다른 MI 프로젝트로 갈 수 있다.
    """
    block = (project_settings or {}).get(STACK_KEY)
    if not isinstance(block, dict):
        return None
    target = AnalysisTarget(
        connection_id=_clean(block.get(STACK_ANALYSIS_CONNECTION)),
        workspace=_clean(block.get(STACK_MAIU_WORKSPACE)),
        project_id=_clean(block.get(STACK_MAIU_PROJECT)),
    )
    return None if target.empty else target


def with_analysis_target(project_settings: dict | None, target: AnalysisTarget | None) -> dict:
    """MI 선택을 바꾼 새 설정 dict를 돌려준다. `stack` 블록과 설정의 다른 키는 그대로 둔다."""
    current = (project_settings or {}).get(STACK_KEY)
    block = dict(current) if isinstance(current, dict) else {}
    t = target or AnalysisTarget()
    # MI 프로젝트가 바뀌면 저장된 이름도 지운다. 남기면 화면에 이전 프로젝트의 이름이 표시된다.
    if block.get(STACK_MAIU_PROJECT) != t.project_id:
        block.pop(STACK_MAIU_PROJECT_NAME, None)
    block[STACK_ANALYSIS_CONNECTION] = t.connection_id
    block[STACK_MAIU_WORKSPACE] = t.workspace
    block[STACK_MAIU_PROJECT] = t.project_id
    return {**(project_settings or {}), STACK_KEY: block}


def with_analysis_project_name(project_settings: dict | None, name: str | None) -> dict:
    """고른 MI 프로젝트의 이름을 적은 새 설정 dict를 돌려준다.

    MI 프로젝트를 고르지 않았거나 이름이 비었으면 설정을 그대로 돌려준다.
    """
    current = (project_settings or {}).get(STACK_KEY)
    label = _clean(name)
    if not isinstance(current, dict) or not _clean(current.get(STACK_MAIU_PROJECT)) or not label:
        return dict(project_settings or {})
    return {**(project_settings or {}), STACK_KEY: {**current, STACK_MAIU_PROJECT_NAME: label}}


def analysis_project_name_of(project_settings: dict | None) -> str | None:
    """고른 MI 프로젝트의 이름을 꺼낸다. 확인한 적이 없으면 `None`이다."""
    block = (project_settings or {}).get(STACK_KEY)
    if not isinstance(block, dict) or not _clean(block.get(STACK_MAIU_PROJECT)):
        return None
    return _clean(block.get(STACK_MAIU_PROJECT_NAME))


def analysis_project_label(name: str | None, project_id: str) -> str:
    """MI 프로젝트의 화면 표기를 만든다. 이름이 있으면 `이름 · id N`, 없으면 `id N`이다."""
    return f"{name} · id {project_id}" if name and name != project_id else f"id {project_id}"


def storage_target_of(project_settings: dict | None) -> StorageTarget | None:
    """프로젝트 설정의 `stack` 블록에서 저장소 선택을 꺼낸다. 고르지 않았으면 `None`이다."""
    block = (project_settings or {}).get(STACK_KEY)
    if not isinstance(block, dict):
        return None
    target = StorageTarget(
        connection_id=_clean(block.get(STACK_STORAGE_CONNECTION)),
        bucket=_clean(block.get(STACK_BUCKET)),
    )
    return None if target.empty else target


def with_storage_target(project_settings: dict | None, target: StorageTarget | None) -> dict:
    """저장소 선택을 바꾼 새 설정 dict를 돌려준다. `stack` 블록의 MI 선택과 다른 키는 유지한다."""
    current = (project_settings or {}).get(STACK_KEY)
    block = dict(current) if isinstance(current, dict) else {}
    t = target or StorageTarget()
    block[STACK_STORAGE_CONNECTION] = t.connection_id
    block[STACK_BUCKET] = t.bucket
    return {**(project_settings or {}), STACK_KEY: block}


def storage_connection(connection_id: str | None) -> StorageCredentials | None:
    """연결 id의 저장소 값을 돌려준다. 버킷은 연결 설정의 값이다. 읽을 수 없으면 `None`이다.

    메모리에 없는 id는 다른 프로세스가 방금 만든 연결일 수 있다. 이 함수는 DB를 조회할 수 없는
    동기 경로라서 다음 `refresh_if_stale`이 주기를 기다리지 않고 읽도록 표시만 한다.
    """
    global _missed
    if not connection_id:
        return None
    found = _storage_by_id.get(connection_id)
    if found is None:
        _missed = True
    return found if isinstance(found, StorageCredentials) else None


def storage_for_or_none(target: StorageTarget | None) -> StorageCredentials | None:
    """저장소 선택(연결·버킷)에 맞는 저장소 값을 돌려준다. 쓸 수 있는 값이 없으면 `None`이다.

    연결과 버킷이 모두 있어야 한다. 연결의 키로 그 버킷을 쓴다.
    """
    if target is None or not target.complete:
        return None
    base = storage_connection(target.connection_id)
    if base is None:
        return None
    creds = replace(base, bucket=target.bucket or "")
    return creds if creds.complete else None


def storage_for(target: StorageTarget | None) -> StorageCredentials:
    """저장소 선택(연결·버킷)에 맞는 저장소 값을 돌려준다.

    Raises:
        CredentialsMissingError: 연결이나 버킷을 고르지 않았거나 고른 연결을 읽지 못한 경우
            (`storage_not_configured`).
    """
    creds = storage_for_or_none(target)
    if creds is None:
        raise CredentialsMissingError(STORAGE_NOT_CONFIGURED)
    return creds


def storage_for_bucket_or_none(bucket: str | None) -> StorageCredentials | None:
    """버킷 이름으로 그 버킷을 읽을 저장소 값을 찾는다. 쓸 수 있는 값이 없으면 `None`이다.

    URI에 담긴 버킷의 객체를 읽는 경로가 쓴다. 프로젝트가 고른 버킷이면 그 프로젝트의 연결을,
    영상 원본이 있는 버킷이면 그 영상에 고정 기록한 연결을,
    연결 설정에 적은 버킷이면 그 연결을 쓴다.
    세 곳 모두에 없는 버킷은 읽지 않는다.
    """
    name = (bucket or "").strip()
    if not name:
        return None
    owner = _bucket_owner.get(name)
    if owner:
        found = storage_for_or_none(StorageTarget(owner, name))
        if found is not None:
            return found
    for conn_id in sorted(_storage_by_id):
        creds = _storage_by_id[conn_id]
        if isinstance(creds, StorageCredentials) and creds.bucket == name and creds.complete:
            return creds
    # 다른 프로세스가 방금 기록한 버킷일 수 있다.
    # 다음 `refresh_if_stale`이 주기를 기다리지 않고 다시 읽도록 표시한다.
    global _missed
    _missed = True
    return None


def storage_for_bucket(bucket: str | None) -> StorageCredentials:
    """버킷 이름으로 그 버킷을 읽을 저장소 값을 찾는다.

    Raises:
        CredentialsMissingError: 그 버킷을 읽을 연결이 없는 경우(`storage_not_configured`).
    """
    creds = storage_for_bucket_or_none(bucket)
    if creds is None:
        raise CredentialsMissingError(STORAGE_NOT_CONFIGURED)
    return creds


def project_bucket(project_settings: dict | None) -> str:
    """프로젝트가 고른 버킷 이름을 돌려준다. 고르지 않았으면 빈 문자열이다.

    MI 워크스페이스 연동 버킷과 비교할 때 쓴다. 객체를 읽고 쓰는 경로는 `storage_for`를 쓴다.
    """
    target = storage_target_of(project_settings)
    return (target.bucket or "") if target is not None else ""


def any_storage_connection() -> bool:
    """키를 읽을 수 있는 저장소 연결이 하나라도 있으면 참. 프로젝트 생성 가능 여부 판정에 쓴다."""
    return any(isinstance(c, StorageCredentials) and c.has_keys for c in _storage_by_id.values())


def any_analysis_connection() -> bool:
    """키를 읽을 수 있는 MI 연결이 하나라도 있으면 참. 프로젝트 생성 가능 여부 판정에 쓴다."""
    return any(isinstance(c, AnalysisCredentials) and c.has_keys
               for c in _analysis_by_id.values())


def storage_connection_loaded(connection_id: str) -> bool:
    """연결 id의 저장소 값이 이 프로세스 메모리에 있으면 참. 읽지 못한 연결도 있는 것으로 본다."""
    return connection_id in _storage_by_id


async def ensure_storage_loaded(db: AsyncSession, target: StorageTarget | None) -> None:
    """저장소 선택의 연결이나 버킷이 메모리에 없으면 바로 다시 읽는다.

    다른 프로세스가 방금 저장한 선택은 이 프로세스의 갱신 주기 전까지 메모리에 없다.
    그 사이 업로드 서명이나 버킷 조회가 실패로 끝나지 않게 한다.
    """
    if target is None or target.empty:
        return
    missing_conn = bool(target.connection_id) and not storage_connection_loaded(
        target.connection_id or "")
    missing_bucket = bool(target.bucket) and target.bucket not in _bucket_owner
    if missing_conn or missing_bucket:
        await refresh(db)


def analysis_for_or_none(target: AnalysisTarget | None) -> AnalysisCredentials | None:
    """MI 선택(연결·워크스페이스·MI 프로젝트)에 맞는 분석 값을 돌려준다. 없으면 `None`이다.

    세 필드가 모두 있어야 한다. 고른 연결을 읽지 못했으면 다른 연결로 넘어가지 않고
    `None`을 돌려준다.
    """
    if target is None or not target.complete:
        return None
    found = _analysis_by_id.get(target.connection_id or "")
    if not isinstance(found, AnalysisCredentials):
        return None
    creds = replace(found, workspace=target.workspace or "", project_id=target.project_id or "")
    return creds if creds.complete else None


def analysis_for(target: AnalysisTarget | None) -> AnalysisCredentials:
    """MI 선택에 맞는 분석 값을 돌려준다.

    Raises:
        CredentialsMissingError: 세 필드 중 하나를 고르지 않았거나 고른 연결을 읽지 못한 경우
            (`analysis_not_configured`).
    """
    creds = analysis_for_or_none(target)
    if creds is None:
        raise CredentialsMissingError(ANALYSIS_NOT_CONFIGURED)
    return creds


def analysis_connection(connection_id: str | None) -> AnalysisCredentials | None:
    """연결 id의 분석 값을 돌려준다. 워크스페이스·MI 프로젝트는 비어 있다. 없으면 `None`이다.

    워크스페이스·MI 프로젝트를 고르기 전에 목록을 읽는 경로가 쓴다.
    """
    found = _analysis_by_id.get(connection_id or "")
    return found if isinstance(found, AnalysisCredentials) else None


def analysis_ready_for(target: AnalysisTarget | None) -> bool:
    """MI 선택으로 분석 요청을 보낼 수 있으면 참.

    분석 연동이 꺼진 환경은 가짜 클라이언트가 응답하므로 참이다.
    """
    return not settings.maiu_enabled or analysis_for_or_none(target) is not None


def connection_loaded(connection_id: str) -> bool:
    """연결 id의 분석 값이 이 프로세스 메모리에 있으면 참. 읽지 못한 연결도 있는 것으로 본다."""
    return connection_id in _analysis_by_id


async def ensure_loaded(db: AsyncSession, target: AnalysisTarget | None) -> None:
    """MI 선택의 연결이 메모리에 없으면 바로 다시 읽는다.

    연결을 만든 직후나 다른 프로세스가 방금 고른 연결은 갱신 주기 전까지 메모리에 없다.
    그 사이에 분석 요청이나 폴링이 실패로 세어지지 않게 한다.
    """
    if target is not None and target.connection_id and not connection_loaded(target.connection_id):
        await refresh(db)


async def refresh_if_stale(db: AsyncSession) -> None:
    """마지막으로 읽은 지 `REFRESH_SECONDS`가 지났거나 없는 저장소 연결을 찾았으면 다시 읽는다.

    worker-io 태스크가 시작할 때 부른다.
    """
    if (_missed or _loaded_at is None
            or time.monotonic() - _loaded_at >= REFRESH_SECONDS):
        await refresh(db)


async def run_forever(interval_s: float = REFRESH_SECONDS) -> None:
    """api의 배경 루프에서 연결을 주기마다 다시 읽는다. 갱신이 실패해도 루프는 계속 돈다."""
    from api_gateway.database import SessionLocal

    while True:
        await asyncio.sleep(interval_s)
        try:
            async with SessionLocal() as db:
                await refresh(db)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("연결 갱신 실패(다음 주기에 다시 읽는다): %s", type(e).__name__)


def reset() -> None:
    """메모리의 연결 id별 값과 버킷 소유 연결을 비운다. 테스트가 상태를 처음으로 돌릴 때 쓴다."""
    global _analysis_by_id, _storage_by_id, _bucket_owner, _loaded_at, _missed
    _missed = False
    _analysis_by_id, _storage_by_id, _bucket_owner = {}, {}, {}
    _loaded_at = None
