"""프로젝트별 저장소 대상(저장소 연결·버킷)을 고르는 규칙, 점검, 선택지.

프로젝트 설정의 `stack` 블록에 분석 대상과 함께 저장한다(`platform_credentials.StorageTarget`).
영상 행을 만들 때 이 값을 `extra.storage`에 고정 기록한다(`media.storage.pin_for`).
업로드 서명·변환·비식별화·재생본·썸네일·납품 패키지·문의 첨부는 그 연결과 버킷을 쓴다.

규칙은 이 모듈에만 둔다.
- NCP 저장소를 쓰는 분석 방식(`ncp-mi`·`ncp-shots`)의 프로젝트는 저장소 연결과 버킷을
  기록해야 영상 등록을 받는다. 기록이 없으면 409 `storage_stack_unset`이다. 기본값은 없다.
- 영상이 하나라도 생기면 저장소 대상을 바꿀 수 없다(`stack_lock`). 한 프로젝트의 원본과 산출이
  두 버킷에 나뉘면 정리·재생·납품이 읽을 버킷을 정할 수 없다.
- 그 프로젝트 조직의 `ncp` 연결 중 자격증명이 있고 마지막 점검이 정상인 연결만 고른다.
- 버킷은 그 연결이 허용한 버킷만 고른다(`allowed_buckets`). 허용 범위는 연결 설정의 버킷과
  허용 버킷 목록의 합이고, 연결 편집 권한(최고관리자)으로만 바꾼다. 저장소 키는 보통 계정의
  모든 버킷에 닿으므로, 이 범위가 없으면 프로젝트 관리자가 다른 버킷의 객체를 탐색하고 등록해
  읽을 수 있다. 선택지·저장·탐색·등록이 모두 `require_allowed` 하나로 판정한다.
- 프로젝트가 쓰는 버킷을 허용 범위에서 빼는 연결 수정은 거절한다(`users_losing_bucket`).
  빼려면 그 프로젝트의 저장소를 먼저 다른 버킷으로 바꾼다.
- 저장 전에 실제로 점검한다. 버킷 접근, 목록, 쓰기·읽기·삭제, 업로드 주소 서명, 브라우저 업로드
  허용(CORS)을 확인한다. 영상 등록 직전에 같은 점검을 캐시와 함께 다시 돌린다. 주기 점검도
  같은 점검을 돌려 실패를 알린다. MI 워크스페이스 연동 버킷과의 일치는 `project_stack`이 확인한다.
- CORS가 업로드를 모든 출처(`*`)에 허용하면 경고로 표시한다. 경고는 저장과 등록을 막지 않는다.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Final
from uuid import UUID, uuid4

import anyio
import httpx
from fastapi import HTTPException, status
from reborn_contracts import (
    ProjectStorageStackDTO,
    StorageChoicesDTO,
    StorageConnectionOption,
)
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Connection, Project, User
from api_gateway.services import audit
from api_gateway.services import platform_credentials as pc
from api_gateway.services.analysis import presets
from api_gateway.services.media import storage
from api_gateway.services.ops.diagnostics import Check, exception_reason
from api_gateway.services.stacks.stack_lock import STORAGE_UNSET, lock_for_change, lock_state
from api_gateway.services.transient import is_transient_boto

log = logging.getLogger(__name__)

#: 저장소 연결을 고를 수 있는 마지막 점검 결과.
_SELECTABLE_STATUS: Final = "ok"
#: 영상 등록 직전 점검이 캐시 결과를 재사용하는 시간(초). 점검 버튼과 저장은 캐시를 갱신한다.
CHECK_TTL_SECONDS: Final = 300.0
#: 저장소 대상이 기록되지 않은 프로젝트의 등록을 거부할 때의 오류 코드.
UNSET: Final = STORAGE_UNSET
#: 영상이 생겨 저장소 대상을 바꿀 수 없을 때의 오류 코드.
LOCKED: Final = "storage_stack_locked"
#: 주기 점검이 상태 변화를 남기는 이벤트 종류.
HEALTH_EVENT: Final = "project.storage_check_changed"
#: 점검 객체를 쓰는 경로. 앱 전용 경로(`_diagnostics/`) 아래에 두어 등록 화면이 원본으로
#: 받지 않게 한다.
_CHECK_PREFIX: Final = "_diagnostics/project-check/"
#: 업로드 주소 점검에서 서명한 주소로 올릴 때의 타임아웃(초).
_PUT_TIMEOUT_S: Final = 10.0
#: 브라우저 업로드가 쓰는 방법. 브라우저는 멀티파트 파트를 서명한 주소에 PUT으로 올린다.
_UPLOAD_METHODS: Final = frozenset({"PUT"})
#: S3 버킷 이름 규칙(3~63자, 소문자·숫자·점·하이픈, 처음과 끝은 소문자나 숫자).
_BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
#: 모든 출처를 허용하는 CORS 출처 값.
_ANY_ORIGIN: Final = "*"
#: 연결 설정에서 프로젝트 저장소로 더 허용하는 버킷 목록의 키.
ALLOWED_BUCKETS_KEY: Final = "allowed_buckets"
#: 프로젝트 관리자가 고를 수 있는 버킷 범위를 정하는 연결 설정 키.
#: 연결 수정 감사 기록이 전후 값을 남긴다.
BUCKET_SCOPE_KEYS: Final = frozenset({"bucket", ALLOWED_BUCKETS_KEY})


# ── 사용처·적용 범위 ────────────────────────────────────────────────────────


def connection_ref():
    """프로젝트 설정이 가리키는 저장소 연결 id의 SQL 식을 만든다. 연결의 사용처 집계가 쓴다."""
    return Project.settings[pc.STACK_KEY][pc.STACK_STORAGE_CONNECTION].astext


def supports(project_settings: dict | None) -> bool:
    """프로젝트의 분석 방식이 NCP 저장소를 쓰면 참. 거짓이면 저장소 대상을 고르지 않는다."""
    return presets.needs_storage(presets.of_project(project_settings))


def required(project_settings: dict | None) -> bool:
    """저장소 대상을 기록해야 영상 등록을 받는 프로젝트면 참(`presets.storage_target_required`)."""
    return presets.storage_target_required(presets.of_project(project_settings))


def target_from(body: ProjectStorageStackDTO) -> pc.StorageTarget | None:
    """요청 본문을 저장소 선택으로 바꾼다. 두 값이 모두 비면 `None`이다."""
    target = pc.StorageTarget(
        connection_id=str(body.storage_connection_id) if body.storage_connection_id else None,
        bucket=_clean(body.bucket))
    return None if target.empty else target


def _clean(value: str | None) -> str | None:
    """입력값의 앞뒤 공백을 지운다. 비었으면 `None`이다."""
    return (value or "").strip() or None


# ── 허용 버킷 ────────────────────────────────────────────────────────────────


def clean_bucket_scope(config: dict) -> dict:
    """연결 설정의 허용 버킷 목록을 검사하고 정리한 설정을 돌려준다. 연결 생성·수정이 부른다.

    목록은 버킷 이름 문자열의 목록이어야 한다. 앞뒤 공백을 지우고 빈 항목과 중복을 빼고 정렬한다.
    키가 없으면 설정을 그대로 돌려준다.

    Raises:
        HTTPException: 목록이 아니거나 버킷 이름 규칙에 맞지 않는 항목이 있는 경우
            (422 `connection_allowed_buckets_invalid`).
    """
    if ALLOWED_BUCKETS_KEY not in config:
        return config
    raw = config[ALLOWED_BUCKETS_KEY]
    if not isinstance(raw, list) or not all(isinstance(b, str) for b in raw):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "connection_allowed_buckets_invalid")
    names = sorted({b.strip() for b in raw if b.strip()})
    if not all(_BUCKET_NAME.fullmatch(b) for b in names):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "connection_allowed_buckets_invalid")
    return {**config, ALLOWED_BUCKETS_KEY: names}


def allowed_buckets(config: dict | None) -> list[str]:
    """연결이 프로젝트 저장소로 허용하는 버킷을 정렬해 돌려준다.

    연결 설정의 버킷(점검에 쓰는 버킷)과 허용 버킷 목록의 합이다. 목록이 비었거나 없으면
    설정의 버킷 하나만 허용한다. 프로젝트가 이미 쓰는 다른 버킷은 목록에 있어야 계속 쓴다.
    """
    cfg = config or {}
    names = {str(cfg.get("bucket") or "").strip()}
    extra = cfg.get(ALLOWED_BUCKETS_KEY)
    if isinstance(extra, list):
        names |= {str(b).strip() for b in extra}
    return sorted(n for n in names if n)


def bucket_allowed(config: dict | None, bucket: str | None) -> bool:
    """버킷이 연결의 허용 범위 안이면 참."""
    return bool(bucket) and bucket in allowed_buckets(config)


async def users_losing_bucket(db: AsyncSession, conn: Connection, after: dict) -> list[str]:
    """연결 설정을 `after`로 바꾸면 쓰던 버킷이 허용 범위에서 빠지는 프로젝트 이름을 돌려준다.

    연결 수정이 저장 전에 부른다. 범위에서 빠진 버킷을 쓰는 프로젝트는 등록·탐색·폴더 등록이
    422 `storage_bucket_not_allowed`로 멈추므로, 수정을 거절해 프로젝트 저장소를 먼저 바꾸게 한다.
    판정은 등록 경로와 같은 `bucket_allowed`로 한다. 수정 전에도 범위 밖이던 버킷은 세지 않는다.
    그 프로젝트는 이미 멈춰 있고, 세면 그 연결의 다른 값도 고칠 수 없다.
    삭제한 프로젝트는 세지 않는다. 삭제한 프로젝트는 등록·탐색 경로가 막고 되살리는 경로가 없다.

    Returns:
        프로젝트 이름을 정렬한 목록. 없으면 빈 목록이다.
    """
    rows = (await db.execute(
        select(Project.name, Project.settings[pc.STACK_KEY][pc.STACK_BUCKET].astext)
        .where(Project.deleted_at.is_(None), connection_ref() == str(conn.id))
    )).all()
    return sorted({
        name for name, bucket in rows
        if bucket_allowed(conn.config, bucket) and not bucket_allowed(after, bucket)
    })


async def require_allowed(db: AsyncSession, target: pc.StorageTarget) -> None:
    """저장소 대상의 버킷이 그 연결의 허용 범위 안인지 확인한다.

    저장(`_verify`), 영상 등록 직전 확인(`require_ready`), 저장소 등록 라우트가 모두 이 함수로
    판정한다. 연결을 찾지 못하면 허용 범위를 알 수 없으므로 거절한다.

    Raises:
        HTTPException: 버킷이 허용 범위 밖이거나 연결을 찾지 못한 경우
            (422 storage_bucket_not_allowed).
    """
    try:
        conn_id = UUID(target.connection_id or "")
    except ValueError:
        conn_id = None
    conn = await db.get(Connection, conn_id) if conn_id is not None else None
    if conn is None or not bucket_allowed(conn.config, target.bucket):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "storage_bucket_not_allowed")


# ── 점검 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Result:
    """점검 항목 하나와 저장을 거부할 때 쓸 오류 코드."""

    check: Check
    code: str | None = None


#: 점검 결과 캐시. 키는 `(프로젝트, 연결, 버킷)`이고 값은 `(시각, 결과)`다.
_cache: dict[tuple, tuple[float, list[_Result]]] = {}

_LABELS: Final = {
    "storage_bucket": "저장소 접근",
    "storage_list": "목록 조회",
    "storage_write": "쓰기·읽기·삭제",
    "storage_presign": "업로드 주소 서명",
    "storage_cors": "브라우저 업로드 허용",
}


def _status_of(exc: BaseException) -> tuple[int | None, str]:
    """저장소 예외에서 HTTP 상태와 오류 코드를 꺼낸다. 없으면 `(None, "")`이다."""
    resp = getattr(exc, "response", None) or {}
    meta = resp.get("ResponseMetadata") or {}
    return meta.get("HTTPStatusCode"), str((resp.get("Error") or {}).get("Code") or "")


def _failure(key: str, exc: BaseException) -> _Result:
    """저장소 예외를 점검 항목으로 바꾼다. 일시 오류는 `warn`으로 두어 등록을 허용한다.

    일시 오류(스로틀·5xx·타임아웃)로 등록을 거부하면 저장소가 잠깐 느린 동안 영상 등록이 멈춘다.
    그래서 확인하지 못한 항목을 근거로 정상 요청을 거부하지 않는다.
    """
    label = _LABELS[key]
    code, err = _status_of(exc)
    if is_transient_boto(exc):
        return _Result(Check(key, label, "warn",
                             f"지금 확인하지 못했습니다 · {exception_reason(exc)}",
                             "잠시 뒤 다시 점검합니다."), "storage_unreachable")
    if code == 403 or err in ("AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
        return _Result(Check(key, label, "fail", "권한이 거부됐습니다",
                             "저장소 연결의 키에 이 저장소 권한이 있는지 확인합니다."),
                       "storage_access_denied")
    if code == 404 or err in ("NoSuchBucket", "NotFound", "404"):
        return _Result(Check(key, label, "fail", "저장소가 없습니다",
                             "저장소 이름과 저장소 연결의 주소를 확인합니다."),
                       "storage_bucket_not_found")
    return _Result(Check(key, label, "fail", f"확인 실패 · {exception_reason(exc)}",
                         "저장소 연결의 주소와 키를 확인합니다."), "storage_unreachable")


def _skipped(key: str) -> _Result:
    """앞 단계가 막혀 확인하지 않은 항목을 만든다."""
    return _Result(Check(key, _LABELS[key], "unchecked", "앞 항목이 실패해 확인하지 않았습니다"))


def _cors_result(rules: list[dict] | None, origin: str | None) -> _Result:
    """버킷 CORS 규칙으로 브라우저 업로드가 허용되는지 판정한다. 결과는 `ok`나 `warn`이다.

    등록 경로 중 저장소 등록·폴더 등록은 브라우저가 버킷에 올리지 않으므로 CORS가 필요 없다.
    그래서 CORS 문제는 등록을 거부하지 않고 `warn`으로 알린다. `origin`이 없으면(주기 점검)
    업로드 방법을 허용하는 규칙이 있는지만 확인한다.
    업로드 방법을 모든 출처(`*`)에 허용하는 규칙이 있으면 업로드는 되지만 `warn`이다.
    그 규칙이 있으면 다른 사이트의 화면도 유출된 서명 주소로 버킷에 올릴 수 있다.
    다른 규칙이 화면 출처를 따로 적어도 같은 경고다. 업로드 방법이 없는 규칙(읽기 전용)은
    판정에 쓰지 않는다.
    """
    key, label = "storage_cors", _LABELS["storage_cors"]
    hint = "저장소 CORS 규칙에 이 화면의 주소와 PUT 방법을 허용합니다."
    if not rules:
        return _Result(Check(key, label, "warn", "CORS 규칙이 없어 브라우저 업로드가 막힙니다",
                             hint))
    allowed: str | None = None
    for rule in rules:
        methods = {str(m).upper() for m in rule.get("AllowedMethods") or []}
        origins = {str(o).rstrip("/") for o in rule.get("AllowedOrigins") or []}
        if not methods >= _UPLOAD_METHODS:
            continue
        if _ANY_ORIGIN in origins:
            return _Result(Check(key, label, "warn", "모든 주소에서 올리는 업로드를 허용합니다",
                                 "저장소 CORS 허용 주소를 이 서비스의 웹 주소로 좁힙니다."))
        if origin is None:
            allowed = allowed or "업로드 방법을 허용하는 규칙이 있습니다"
        elif origin.rstrip("/") in origins:
            allowed = f"{origin} 허용"
    if allowed is not None:
        return _Result(Check(key, label, "ok", allowed))
    shown = origin or "이 화면의 주소"
    return _Result(Check(key, label, "warn", f"{shown}에서 올리는 업로드를 허용하지 않습니다",
                         hint))


def _probe_sync(
    project_id: UUID, creds: pc.StorageCredentials, origin: str | None,
) -> list[_Result]:
    """저장소 대상에 실제로 접속해 다섯 가지를 확인한다. 점검 객체를 쓴 뒤 지운다.

    버킷 접근, 목록, 쓰기·읽기·삭제, 서명한 주소로 올리기, CORS 순이다. 버킷에 접근하지 못하면
    나머지는 `unchecked`로 둔다. 업로드 경로와 같은 클라이언트(`media.storage`)를 쓴다.
    """
    keys = list(_LABELS)
    try:
        storage.check_bucket(creds)
    except Exception as e:  # noqa: BLE001
        return [_failure("storage_bucket", e), *(_skipped(k) for k in keys[1:])]
    out = [_Result(Check("storage_bucket", _LABELS["storage_bucket"], "ok", creds.bucket))]
    try:
        storage.list_one(creds)
        out.append(_Result(Check("storage_list", _LABELS["storage_list"], "ok",
                                 "목록 조회 확인")))
    except Exception as e:  # noqa: BLE001
        out.append(_failure("storage_list", e))
    probe_key = f"{_CHECK_PREFIX}{project_id}/{uuid4().hex}.txt"
    body = b"reborn-project-storage-check"
    try:
        storage.put_bytes(probe_key, body, "text/plain", store=creds)
        same = storage.read_bytes(probe_key, store=creds) == body
        storage.delete_object(bucket=creds.bucket, key=probe_key, store=creds)
        out.append(_Result(Check(
            "storage_write", _LABELS["storage_write"], "ok" if same else "fail",
            "점검 객체를 쓰고 읽고 지웠습니다" if same else "쓴 내용과 읽은 내용이 다릅니다",
            None if same else "저장소 연결의 주소를 확인합니다."),
            None if same else "storage_unreachable"))
    except Exception as e:  # noqa: BLE001
        out.append(_failure("storage_write", e))
    out.append(_presign_result(project_id, creds))
    try:
        out.append(_cors_result(storage.bucket_cors(creds), origin))
    except Exception as e:  # noqa: BLE001
        log.info("CORS 규칙을 읽지 못했다 bucket=%s: %s", creds.bucket, type(e).__name__)
        out.append(_Result(Check("storage_cors", _LABELS["storage_cors"], "warn",
                                 f"CORS 규칙을 읽지 못했습니다 · {exception_reason(e)}",
                                 "CORS 읽기 권한이 없으면 클라우드 콘솔에서 확인합니다.")))
    return out


def _presign_result(project_id: UUID, creds: pc.StorageCredentials) -> _Result:
    """서명한 업로드 주소(PUT)를 만들어 실제로 올려 보고 지운다.

    워커와 브라우저는 서명한 주소로만 올린다. 키로 직접 쓰기는 되지만 서명한 주소는 거절되는
    연결(서명 방식·엔드포인트 차이)을 여기서 잡는다.
    """
    key, label = "storage_presign", _LABELS["storage_presign"]
    probe_key = f"{_CHECK_PREFIX}{project_id}/{uuid4().hex}.put.txt"
    try:
        url = storage.presign_put_for(bucket=creds.bucket, key=probe_key,
                                      content_type="text/plain", ttl_seconds=300, store=creds)
    except Exception as e:  # noqa: BLE001
        return _failure(key, e)
    try:
        resp = httpx.put(url, content=b"reborn", headers={"Content-Type": "text/plain"},
                         timeout=_PUT_TIMEOUT_S)
    except httpx.HTTPError as e:
        return _Result(Check(key, label, "warn",
                             f"지금 확인하지 못했습니다 · {exception_reason(e)}",
                             "잠시 뒤 다시 점검합니다."), "storage_unreachable")
    try:
        storage.delete_object(bucket=creds.bucket, key=probe_key, store=creds)
    except Exception:  # noqa: BLE001 — 점검 객체가 남는 것이 판정보다 덜 중요하다
        log.warning("업로드 주소 점검 객체 삭제 실패 key=%s", probe_key)
    if resp.status_code in (200, 201, 204):
        return _Result(Check(key, label, "ok", "서명한 주소로 올리기 확인"))
    if resp.status_code >= 500:
        return _Result(Check(key, label, "warn",
                             f"지금 확인하지 못했습니다 · 응답 {resp.status_code}",
                             "잠시 뒤 다시 점검합니다."), "storage_unreachable")
    return _Result(Check(key, label, "fail",
                         f"서명한 주소로 올리지 못했습니다 · 응답 {resp.status_code}",
                         "저장소 연결의 주소와 리전을 확인합니다."), "storage_access_denied")


async def _probe(
    project_id: UUID, target: pc.StorageTarget, origin: str | None,
) -> list[_Result]:
    """저장소 대상을 점검한다. 고르지 않았거나 자격증명을 읽지 못하면 첫 항목에서 멈춘다."""
    if not target.complete:
        first = _Result(Check("storage_bucket", _LABELS["storage_bucket"], "fail",
                              "저장소 대상을 고르지 않았습니다",
                              "저장소 연결과 저장소를 고르고 저장합니다."), UNSET)
        return [first, *(_skipped(k) for k in list(_LABELS)[1:])]
    creds = pc.storage_for_or_none(target)
    if creds is None:
        first = _Result(Check("storage_bucket", _LABELS["storage_bucket"], "fail",
                              "저장소 연결의 자격증명을 읽지 못했습니다",
                              "자격증명 연결에서 그 연결의 자격증명을 다시 저장합니다."),
                        pc.STORAGE_NOT_CONFIGURED)
        return [first, *(_skipped(k) for k in list(_LABELS)[1:])]
    return await anyio.to_thread.run_sync(_probe_sync, project_id, creds, origin)


def _cache_key(project_id: UUID, target: pc.StorageTarget) -> tuple:
    """점검 캐시 키를 만든다. 연결이나 버킷이 바뀌면 다른 키가 된다."""
    return (project_id, target.connection_id, target.bucket)


async def _results(
    db: AsyncSession, project_id: UUID, target: pc.StorageTarget, *, fresh: bool,
    origin: str | None = None,
) -> list[_Result]:
    """점검 결과를 돌려준다. `fresh`가 거짓이면 `CHECK_TTL_SECONDS` 안의 캐시 결과를 재사용한다."""
    key = _cache_key(project_id, target)
    hit = _cache.get(key)
    now = time.monotonic()
    if not fresh and hit is not None and now - hit[0] < CHECK_TTL_SECONDS:
        return hit[1]
    await pc.ensure_storage_loaded(db, target)
    results = await _probe(project_id, target, origin)
    _cache[key] = (now, results)
    return results


async def check(
    db: AsyncSession, project: Project, *, fresh: bool = True, origin: str | None = None,
) -> list[Check]:
    """프로젝트가 저장한 저장소 대상을 점검한다. 기록이 없으면 첫 항목이 실패다.

    점검 버튼은 `fresh=True`와 화면의 주소(`origin`)로 호출해 그 결과로 캐시를 갱신한다.
    """
    target = pc.storage_target_of(project.settings) or pc.StorageTarget()
    return [r.check for r in await _results(db, project.project_id, target, fresh=fresh,
                                            origin=origin)]


async def require_ready(db: AsyncSession, project: Project) -> None:
    """영상 등록 직전에 저장소 대상이 기록되어 있고 점검을 통과하는지 확인한다.

    점검은 `CHECK_TTL_SECONDS` 동안 캐시한다. 일시 오류와 CORS(`warn`)는 통과시킨다.

    Raises:
        HTTPException: 저장소 대상을 기록하지 않은 경우(409 storage_stack_unset),
            버킷이 연결의 허용 범위 밖인 경우(422 storage_bucket_not_allowed),
            점검이 실패한 경우(409, 실패한 항목의 사유 문장).
    """
    if not required(project.settings):
        return
    target = pc.storage_target_of(project.settings)
    if target is None or not target.complete:
        raise HTTPException(status.HTTP_409_CONFLICT, UNSET)
    await require_allowed(db, target)
    for r in await _results(db, project.project_id, target, fresh=False):
        if r.check.status == "fail":
            raise HTTPException(status.HTTP_409_CONFLICT, r.check.detail)


# ── 선택지 ──────────────────────────────────────────────────────────────────


def dto(target: pc.StorageTarget | None) -> ProjectStorageStackDTO:
    """선택을 응답 형식으로 바꾼다. 연결 id가 UUID 형식이 아니면 비운다."""
    t = target or pc.StorageTarget()
    try:
        cid = UUID(t.connection_id) if t.connection_id else None
    except ValueError:
        cid = None
    return ProjectStorageStackDTO(storage_connection_id=cid, bucket=t.bucket)


def _selectable(conn: Connection) -> bool:
    """자격증명이 있고 마지막 점검이 정상인 연결이면 참."""
    return conn.secret_ciphertext is not None and conn.last_status == _SELECTABLE_STATUS


async def _candidates(
    db: AsyncSession, org_id: UUID, *, keep: str | None = None,
) -> list[Connection]:
    """고를 수 있는 후보 연결을 이름 순으로 읽는다. 그 조직의 NCP 연결이다.

    `keep`은 이미 저장한 연결 id다. 다른 조직의 연결이어도 목록에 넣어 화면이 저장한 값을
    이름으로 표시하게 한다. 새로 고르는 저장(`_verify`)은 `keep` 없이 호출해 그 조직의 연결만
    받는다.
    """
    cond = Connection.org_id == org_id
    if keep:
        cond = cond | (cast(Connection.id, String) == keep)
    return list((await db.scalars(
        select(Connection).where(cond, Connection.provider == pc.STORAGE_PROVIDER)
        .order_by(Connection.name)
    )).all())


async def choices(
    db: AsyncSession, project: Project, *, connection_id: UUID | None = None,
) -> StorageChoicesDTO:
    """현재 선택·잠금·선택지를 돌려준다. 버킷 목록을 읽지 못하면 사유 문장을 싣는다.

    `connection_id`로 고른 연결의 키로 버킷 목록을 읽는다. 연결을 주지 않으면 읽지 않는다.
    분석 방식이 NCP 저장소를 쓰지 않으면 외부 API를 호출하지 않는다.
    버킷 목록에는 그 연결이 허용한 버킷(`allowed_buckets`)만 싣는다. 키가 닿는 다른 버킷은
    이름도 내보내지 않는다.

    Raises:
        HTTPException: 고를 수 없는 연결로 목록을 읽으려는 경우(422 connection_not_found).
    """
    await pc.refresh(db)
    target = pc.storage_target_of(project.settings)
    lock = await lock_state(db, project.project_id)
    conns = await _candidates(db, project.org_id,
                              keep=target.connection_id if target else None)
    out = StorageChoicesDTO(
        selected=dto(target),
        supported=supports(project.settings),
        required=required(project.settings),
        locked=lock.locked,
        video_count=lock.videos,
        deleted_video_count=lock.deleted,
        connections=[
            StorageConnectionOption(
                id=c.id, name=c.name, has_secret=c.secret_ciphertext is not None,
                last_status=c.last_status, selectable=_selectable(c),
                bucket=str((c.config or {}).get("bucket") or "").strip() or None,
                allowed_buckets=allowed_buckets(c.config))
            for c in conns
        ],
        browse_connection_id=connection_id,
    )
    if connection_id is not None and all(c.id != connection_id for c in conns):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "connection_not_found")
    if not out.supported or connection_id is None:
        return out
    creds = pc.storage_connection(str(connection_id))
    if creds is None:
        out.error = "저장소 연결의 자격증명을 읽지 못했습니다"
        return out
    try:
        rows = await anyio.to_thread.run_sync(lambda: storage.list_buckets(None, store=creds))
    except Exception as e:  # noqa: BLE001
        log.warning("버킷 목록 조회 실패: %s", type(e).__name__)
        out.error = f"저장소 목록을 읽지 못했습니다 · {exception_reason(e)}"
        return out
    allowed = set(allowed_buckets(next(c.config for c in conns if c.id == connection_id)))
    out.buckets = [r["name"] for r in rows if r["name"] in allowed]
    return out


# ── 저장 ─────────────────────────────────────────────────────────────────────


async def _verify(
    db: AsyncSession, project: Project, target: pc.StorageTarget, origin: str | None,
) -> None:
    """저장하려는 선택이 규칙에 맞는지 외부 점검까지 포함해 확인한다.

    Raises:
        HTTPException: 버킷 이름이 규칙에 맞지 않는 경우(422 storage_bucket_invalid),
            연결이 후보가 아닌 경우(422 connection_not_found),
            연결의 서비스가 다른 경우(422 connection_provider_mismatch),
            연결의 마지막 점검이 정상이 아닌 경우(422 connection_not_verified),
            버킷이 연결의 허용 범위 밖인 경우(422 storage_bucket_not_allowed),
            자격증명을 읽지 못한 경우(409 storage_not_configured),
            점검이 실패한 경우(422 storage_bucket_not_found·storage_access_denied),
            저장소를 지금 읽지 못한 경우(503 storage_unreachable).
    """
    if not _BUCKET_NAME.fullmatch(target.bucket or ""):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "storage_bucket_invalid")
    await pc.refresh(db)
    conn = await db.get(Connection, UUID(target.connection_id or ""))
    if conn is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "connection_not_found")
    if conn.provider != pc.STORAGE_PROVIDER:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "connection_provider_mismatch")
    if conn.id not in {c.id for c in await _candidates(db, project.org_id)}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "connection_not_found")
    if not _selectable(conn):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "connection_not_verified")
    # 허용 범위는 외부 점검보다 먼저 본다. 범위 밖 버킷에 점검 객체를 쓰지 않는다.
    await require_allowed(db, target)
    for r in await _results(db, project.project_id, target, fresh=True, origin=origin):
        if r.check.status not in ("fail", "warn") or r.code is None:
            continue
        if r.code == pc.STORAGE_NOT_CONFIGURED:
            raise HTTPException(status.HTTP_409_CONFLICT, r.code)
        if r.code == "storage_unreachable":
            # 확인하지 못한 선택은 저장을 거부한다. 저장한 뒤 첫 등록에서 거부되면 원인을
            # 찾기 어렵다.
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, r.code)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, r.code)


async def require_unlocked(
    db: AsyncSession, project: Project, after: pc.StorageTarget | None,
) -> None:
    """잠긴 프로젝트의 저장소 대상이 바뀌면 409를 던진다. 같은 값을 다시 보내는 것은 허용한다.

    다른 거절 사유보다 먼저 호출한다. 잠긴 프로젝트는 어떤 값을 보내도 바꿀 수 없다.
    버킷 불일치 같은 다른 사유를 먼저 돌려주면 그 값만 고치면 될 것처럼 읽힌다.

    Raises:
        HTTPException: 영상이 있는 프로젝트의 선택을 바꾸려는 경우(409 storage_stack_locked).
    """
    current = pc.storage_target_of(project.settings)
    if (current or pc.StorageTarget()) == (after or pc.StorageTarget()):
        return
    if (await lock_state(db, project.project_id)).locked:
        raise HTTPException(status.HTTP_409_CONFLICT, LOCKED)


async def save(
    db: AsyncSession, project: Project, body: ProjectStorageStackDTO, actor: User,
    *, origin: str | None = None,
) -> ProjectStorageStackDTO:
    """프로젝트의 저장소 대상을 저장한다. 두 값을 모두 비우면 기록을 지운다.

    연결과 버킷 중 하나만 고르면 422로 거부한다. 같은 값을 다시 보내면 아무것도 바꾸지 않는다.
    바꾼 값은 `project.updated` 감사 기록의 `changed.storage_stack`에 남긴다.
    MI 워크스페이스 연동 버킷과의 일치는 호출부(`project_stack.save_storage`)가 먼저 확인한다.
    점검이 끝난 뒤 프로젝트 행을 잠그고 영상 수를 다시 센다(`stack_lock.lock_for_change`).
    점검하는 동안 첫 영상이 생겼으면 409로 거부한다.

    Raises:
        HTTPException: NCP 저장소를 쓰지 않는 분석 방식인 경우(422 storage_stack_unsupported),
            연결과 버킷 중 하나를 비운 경우(422 storage_stack_incomplete),
            영상이 있는 프로젝트의 선택을 바꾸는 경우(409 storage_stack_locked),
            그 밖에 `_verify`가 거부하는 경우.
    """
    if not supports(project.settings):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "storage_stack_unsupported")
    after = target_from(body)
    if after is not None and not after.complete:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "storage_stack_incomplete")
    current = pc.storage_target_of(project.settings)
    if (current or pc.StorageTarget()) == (after or pc.StorageTarget()):
        return dto(current)
    await require_unlocked(db, project, after)
    if after is not None:
        await _verify(db, project, after, origin)
    lock = await lock_for_change(db, project)
    current = pc.storage_target_of(project.settings)
    if (current or pc.StorageTarget()) == (after or pc.StorageTarget()):
        await db.commit()
        return dto(current)
    if lock.locked:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, LOCKED)
    project.settings = pc.with_storage_target(project.settings, after)
    audit.emit(
        db, event_type="project.updated", target_kind="project",
        target_id=project.project_id, actor_user_id=actor.user_id,
        project_id=project.project_id,
        payload={"changed": {"storage_stack": {
            "from": current.as_dict() if current else None,
            "to": after.as_dict() if after else None,
        }}},
    )
    await db.commit()
    # 이 프로세스가 새로 고른 연결을 바로 쓰도록 자격증명을 다시 읽는다.
    # 다른 프로세스는 갱신 주기에 읽는다.
    await pc.refresh(db)
    return dto(after)


async def users_by_connection(
    db: AsyncSession, org_id: UUID | None = None,
) -> list[tuple[UUID, str, str]]:
    """저장소 연결을 고른 프로젝트를 `(연결의 조직, 프로젝트 이름, 연결 이름)`으로 모은다.

    연결 목록의 사용처와 삭제 판단이 쓴다. 조직은 연결의 `org_id`를 쓴다.
    `org_id`를 주면 그 조직의 연결만 센다.
    """
    q = (
        select(Connection.org_id, Project.name, Connection.name)
        .join(Connection, cast(Connection.id, String) == connection_ref())
    )
    if org_id is not None:
        q = q.where(Connection.org_id == org_id)
    return [(o, p, c) for o, p, c in (await db.execute(q)).all()]
