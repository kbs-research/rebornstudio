"""프로젝트별 분석 대상(MI 연결·워크스페이스·MI 프로젝트)을 고르는 규칙, 점검, 선택지.

프로젝트 설정의 `stack` 블록에 저장한다(`platform_credentials.AnalysisTarget`).
분석 요청은 이 값으로 클라이언트를 고른다. 요청한 잡에는 실제로 쓴 값을 기록한다
(`maiu.client.pinned_target`). 저장소 연결·버킷은 같은 블록에 두고 `project_storage`가 고른다.
잠금(`stack_lock`)과 점검 결과 형식(`Check` 목록)은 두 모듈이 함께 쓴다. 영상 등록 게이트(`gate`)와
주기 점검(`recheck_all`)에서는 이 모듈이 두 점검을 함께 호출한다.

규칙은 이 모듈에만 둔다.
- MI를 쓰는 분석 방식의 프로젝트는 분석 대상을 기록해야 영상 등록과 분석 요청을 받는다
  (분석 연동이 켜진 서버에서). 기록이 없으면 409 `analysis_stack_unset`이다.
- 영상이 하나라도 생기면(삭제한 영상과 올리는 중인 영상도 센다) 분석 대상을 바꿀 수 없다.
  한 프로젝트의 에셋이 두 MI 프로젝트에 나뉘면 폴링·정리·재분석이 조회할 MI 프로젝트를 정할 수 없다.
- 그 프로젝트 조직의 `maiu` 연결 중 자격증명이 있고 마지막 점검이 정상인 연결만 고른다.
  연결·워크스페이스·MI 프로젝트 세 값을 함께 고른다. 기본값은 없다.
- 저장 전에 실제로 점검한다. 워크스페이스 접근, 연동 저장소가 그 프로젝트의 저장소와 같은지,
  MI 프로젝트가 있는지, 에셋 목록을 읽을 수 있는지 확인한다. 영상 등록과 분석 요청 직전에
  같은 점검을 캐시와 함께 다시 돌린다. 주기 점검도 같은 점검을 돌려 실패를 알린다.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import (
    AnalysisChoicesDTO,
    AnalysisConnectionOption,
    MaiuProjectOption,
    MaiuWorkspaceOption,
    ProjectAnalysisStackDTO,
    ProjectStorageStackDTO,
)
from sqlalchemy import String, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import Connection, DomainEvent, Project, User
from api_gateway.services import audit, authz
from api_gateway.services import platform_credentials as pc
from api_gateway.services.analysis import presets
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import provisioning
from api_gateway.services.maiu.naver_client import MaiuTransientError
from api_gateway.services.ops.diagnostics import Check, exception_reason
from api_gateway.services.stacks import project_storage
from api_gateway.services.stacks.stack_lock import (
    ANALYSIS_UNSET,
    LockState,
    lock_for_change,
    lock_state,
)
from api_gateway.services.transient import TRANSIENT_STATUS

log = logging.getLogger(__name__)

#: MI 연결을 고를 수 있는 마지막 점검 결과.
_SELECTABLE_STATUS: Final = "ok"
#: 목록 조회만 하는 클라이언트에 넘기는 임시 값. 목록 경로는 이 값을 쓰지 않는다.
_PLACEHOLDER: Final = "-"
#: 영상 등록·분석 요청 직전 점검이 캐시 결과를 재사용하는 시간(초).
#: 점검 버튼과 저장은 캐시를 갱신한다.
CHECK_TTL_SECONDS: Final = 300.0
#: 분석 대상이 기록되지 않은 프로젝트의 등록·분석 요청을 거부할 때의 오류 코드.
UNSET: Final = ANALYSIS_UNSET
#: 영상이 생겨 분석 대상을 바꿀 수 없을 때의 오류 코드.
LOCKED: Final = "analysis_stack_locked"
#: 분석 방식이 MI를 쓰지 않는 프로젝트에 분석 대상·진단을 요청할 때의 오류 코드.
UNSUPPORTED: Final = "analysis_stack_unsupported"
#: 주기 점검이 상태 변화를 남기는 이벤트 종류.
HEALTH_EVENT: Final = "project.analysis_check_changed"

_STATUS_IN_MESSAGE = re.compile(r"→\s*(\d{3})\b")


# ── 사용처·잠금 ──────────────────────────────────────────────────────────────


def connection_ref():
    """프로젝트 설정이 가리키는 MI 연결 id의 SQL 식을 만든다. 연결의 사용처 집계가 쓴다."""
    return Project.settings[pc.STACK_KEY][pc.STACK_ANALYSIS_CONNECTION].astext


async def users_by_connection(
    db: AsyncSession, org_id: UUID | None = None,
) -> list[tuple[UUID, str, str]]:
    """MI 연결이나 저장소 연결을 고른 프로젝트를 `(연결의 조직, 프로젝트, 연결 이름)`으로 모은다.

    저장소 연결 쪽은 `project_storage.users_by_connection`이 센다.

    연결 목록의 사용처와 삭제 판단이 이름 참조(캡션 품질검수·비식별화)와 같은 키로 세도록
    연결 이름으로 바꿔 돌려준다. 조직은 연결의 `org_id`를 쓴다. `org_id`를 주면 그 조직의 연결만
    센다.
    """
    q = (
        select(Connection.org_id, Project.name, Connection.name)
        .join(Connection, cast(Connection.id, String) == connection_ref())
    )
    if org_id is not None:
        q = q.where(Connection.org_id == org_id)
    rows = [(o, p, c) for o, p, c in (await db.execute(q)).all()]
    return rows + await project_storage.users_by_connection(db, org_id)


def supports(project_settings: dict | None) -> bool:
    """프로젝트의 분석 방식이 MI를 쓰면 참. 거짓이면 분석 대상을 고르지 않는다."""
    return presets.needs_analysis(presets.of_project(project_settings))


def require_supported(project_settings: dict | None) -> None:
    """분석 방식이 MI를 쓰지 않으면 422를 던진다. 진단·점검·저장이 같은 판정을 쓴다.

    샷 검출처럼 MI를 쓰지 않는 프로젝트에 분석 대상을 받으면 `stack` 블록이 그 프로젝트에
    잘못 기록된다.

    Raises:
        HTTPException: MI를 쓰지 않는 분석 방식인 경우(422 analysis_stack_unsupported).
    """
    if not supports(project_settings):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, UNSUPPORTED)


def required(project_settings: dict | None) -> bool:
    """분석 대상을 기록해야 영상 등록·분석 요청을 받는 프로젝트면 참(`presets.target_required`)."""
    return presets.target_required(presets.of_project(project_settings))


def require_unlocked_or_same(
    current: pc.AnalysisTarget | None, new: pc.AnalysisTarget | None, lock: LockState,
) -> None:
    """잠긴 프로젝트의 선택이 바뀌면 409를 던진다. 같은 값을 다시 저장하는 것은 허용한다.

    Raises:
        HTTPException: 영상이 있는 프로젝트의 선택을 바꾸려는 경우(409 analysis_stack_locked).
    """
    same = (current or pc.AnalysisTarget()) == (new or pc.AnalysisTarget())
    if lock.locked and not same:
        raise HTTPException(status.HTTP_409_CONFLICT, LOCKED)


# ── 점검 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Result:
    """점검 항목 하나와 저장을 거부할 때 쓸 오류 코드."""

    check: Check
    code: str | None = None
    #: MI 프로젝트 줄이 정상일 때 그 프로젝트의 이름. 저장이 이름을 함께 기록한다.
    name: str | None = None


#: 점검 결과 캐시. 키는 `(프로젝트, 분석 대상, 이 서버의 저장소)`이고 값은 `(시각, 결과)`다.
_cache: dict[tuple, tuple[float, list[_Result]]] = {}


def _status_of(exc: BaseException) -> int | None:
    """예외에서 HTTP 상태 코드를 꺼낸다. 없으면 `None`이다."""
    found = _STATUS_IN_MESSAGE.search(str(exc))
    return getattr(exc, "status", None) or (int(found.group(1)) if found else None)


def _transient(exc: BaseException) -> bool:
    """재시도하면 해소될 수 있는 외부 조회 예외(스로틀·5xx·타임아웃)면 참.

    점검과 선택지 조회가 함께 쓴다.
    """
    return isinstance(exc, MaiuTransientError) or _status_of(exc) in TRANSIENT_STATUS


def _failure(key: str, label: str, exc: BaseException, *, missing: str) -> _Result:
    """외부 조회 예외를 점검 항목으로 바꾼다. 일시 오류는 `warn`으로 두어 등록을 허용한다.

    일시 오류(스로틀·5xx·타임아웃)로 등록을 거부하면 분석 서비스가 잠깐 느린 동안 영상 등록이
    멈춘다. 그래서 확인하지 못한 항목을 근거로 정상 요청을 거부하지 않는다.
    """
    code = _status_of(exc)
    if _transient(exc):
        return _Result(Check(key, label, "warn",
                             f"지금 확인하지 못했습니다 · {exception_reason(exc)}",
                             "잠시 뒤 다시 점검합니다."), "maiu_unreachable")
    if code in (401, 403):
        return _Result(Check(key, label, "fail", "인증이 거부됐습니다",
                             "분석 연결의 키와 MI 권한을 확인합니다."), "maiu_access_denied")
    if code == 404 or provisioning.is_not_found(exc):
        return _Result(Check(key, label, "fail", missing,
                             "분석 대상에서 다른 값을 고르거나 MI 콘솔에서 확인합니다."),
                       "maiu_target_not_found")
    return _Result(Check(key, label, "fail", f"조회 실패 · {exception_reason(exc)}",
                         "분석 연결의 주소와 키를 확인합니다."), "maiu_unreachable")


def _skipped(key: str, label: str) -> _Result:
    """앞 단계가 막혀 확인하지 않은 항목을 만든다."""
    return _Result(Check(key, label, "unchecked", "앞 항목이 실패해 확인하지 않았습니다"))


async def _probe(target: pc.AnalysisTarget, mine: str) -> list[_Result]:
    """분석 대상에 실제로 접속해 네 가지를 확인한다. 외부 API를 호출한다.

    워크스페이스 접근, 연동 저장소 일치, MI 프로젝트 존재, 에셋 목록 조회 권한 순이다.
    분석 연동이 꺼진 서버는 가짜 클라이언트가 응답하므로 외부를 호출하지 않는다.
    그 서버에서 분석 대상을 고르지 않았으면 연동이 꺼져 있다는 정상 항목 하나만 돌려준다.
    `mine`은 그 프로젝트가 쓰는 버킷이다(`platform_credentials.project_bucket`).
    """
    labels = {"maiu_workspace": "분석 워크스페이스", "maiu_bucket": "연결된 저장소",
              "maiu_project": "분석 프로젝트", "maiu_assets": "에셋 조회 권한"}
    if not target.complete and not settings.maiu_enabled:
        # 분석 연동이 꺼진 서버는 분석 대상 없이 가짜 클라이언트가 분석 요청을 처리한다.
        # 분석 대상을 고르지 않은 상태를 실패로 표시하면
        # 막히지 않은 등록과 분석이 막힌 것처럼 보인다.
        return [_Result(Check("maiu_enabled", "분석 연동", "ok",
                              maiu_client.FAKE_MODE_DETAIL, maiu_client.FAKE_MODE_HINT))]
    if not target.complete:
        first = _Result(Check("maiu_workspace", labels["maiu_workspace"], "fail",
                              "분석 대상을 고르지 않았습니다",
                              "분석 연결·워크스페이스·분석 프로젝트를 고르고 저장합니다."), UNSET)
        return [first, *(_skipped(k, v) for k, v in list(labels.items())[1:])]
    try:
        cli = maiu_client.get_client(target)
    except pc.CredentialsMissingError:
        first = _Result(Check("maiu_workspace", labels["maiu_workspace"], "fail",
                              "분석 연결의 자격증명을 읽지 못했습니다",
                              "자격증명 연결에서 그 연결의 자격증명을 다시 저장합니다."),
                        pc.ANALYSIS_NOT_CONFIGURED)
        return [first, *(_skipped(k, v) for k, v in list(labels.items())[1:])]
    ws_name = getattr(cli, "workspace", None) or target.workspace or ""
    try:
        ws = provisioning.unwrap(await cli.get_workspace())
    except Exception as e:  # noqa: BLE001
        first = _failure("maiu_workspace", labels["maiu_workspace"], e,
                         missing=f"워크스페이스 {ws_name}가 없습니다")
        return [first, *(_skipped(k, v) for k, v in list(labels.items())[1:])]
    out = [_Result(Check("maiu_workspace", labels["maiu_workspace"], "ok",
                         str(ws.get("workspaceName") or ws_name)))]
    linked = str(ws.get("bucketName") or "")
    if provisioning.bucket_matches(linked, mine):
        out.append(_Result(Check("maiu_bucket", labels["maiu_bucket"], "ok", f"{linked} 일치")))
    else:
        out.append(_Result(Check(
            "maiu_bucket", labels["maiu_bucket"], "fail",
            f"워크스페이스에 연결된 저장소는 {linked or '없음'}인데 "
            f"이 프로젝트가 쓰는 저장소는 {mine or '없음'}입니다",
            "저장소가 같은 워크스페이스를 고르면 풀립니다."), provisioning.BUCKET_MISMATCH))
    pid = target.project_id or getattr(cli, "project_id", "") or ""
    try:
        names = {o.project_id: o.name for o in _project_options(await cli.list_projects(), None)}
    except Exception as e:  # noqa: BLE001
        out.append(_failure("maiu_project", labels["maiu_project"], e,
                            missing=f"분석 프로젝트 id {pid}가 없습니다"))
        out.append(_skipped("maiu_assets", labels["maiu_assets"]))
        return out
    if pid not in names:
        out.append(_Result(Check("maiu_project", labels["maiu_project"], "fail",
                                 f"워크스페이스에 분석 프로젝트 id {pid}가 없습니다",
                                 "분석 대상에서 있는 프로젝트를 고릅니다."),
                           "maiu_project_not_found"))
        out.append(_skipped("maiu_assets", labels["maiu_assets"]))
        return out
    out.append(_Result(Check("maiu_project", labels["maiu_project"], "ok",
                             pc.analysis_project_label(names[pid], pid)), name=names[pid]))
    try:
        await cli.list_assets(page_size=1)
        out.append(_Result(Check("maiu_assets", labels["maiu_assets"], "ok",
                                 "에셋 목록 조회 확인")))
    except Exception as e:  # noqa: BLE001
        out.append(_failure("maiu_assets", labels["maiu_assets"], e,
                            missing=f"분석 프로젝트 id {pid}를 찾지 못했습니다"))
    return out


def _cache_key(project_id: UUID, target: pc.AnalysisTarget, mine: str) -> tuple:
    """점검 캐시 키를 만든다. 분석 대상이나 그 프로젝트의 저장소가 바뀌면 다른 키가 된다."""
    return (project_id, target.connection_id, target.workspace, target.project_id, mine)


async def _results(
    db: AsyncSession, project: Project, target: pc.AnalysisTarget, *, fresh: bool,
) -> list[_Result]:
    """점검 결과를 돌려준다. `fresh`가 거짓이면 `CHECK_TTL_SECONDS` 안의 캐시 결과를 재사용한다."""
    mine = pc.project_bucket(project.settings)
    key = _cache_key(project.project_id, target, mine)
    hit = _cache.get(key)
    now = time.monotonic()
    if not fresh and hit is not None and now - hit[0] < CHECK_TTL_SECONDS:
        return hit[1]
    await pc.ensure_loaded(db, target)
    results = await _probe(target, mine)
    _cache[key] = (now, results)
    return results


async def check(
    db: AsyncSession, project: Project, *, fresh: bool = True,
) -> list[Check]:
    """프로젝트가 저장한 분석 대상을 점검한다. 기록이 없으면 첫 항목이 실패다.

    점검 버튼은 `fresh=True`로 호출해 그 결과로 캐시를 갱신한다.
    """
    target = pc.analysis_target_of(project.settings) or pc.AnalysisTarget()
    return [r.check for r in await _results(db, project, target, fresh=fresh)]


async def require_ready(db: AsyncSession, project: Project) -> None:
    """영상 등록·분석 요청 직전에 분석 대상이 기록되어 있고 점검을 통과하는지 확인한다.

    점검은 `CHECK_TTL_SECONDS` 동안 캐시한다. 일시 오류(`warn`)는 통과시킨다.

    Raises:
        HTTPException: 분석 대상을 기록하지 않은 경우(409 analysis_stack_unset),
            점검이 실패한 경우(409, 실패한 항목의 사유 문장).
    """
    if not required(project.settings):
        return
    target = pc.analysis_target_of(project.settings)
    if target is None or not target.complete:
        raise HTTPException(status.HTTP_409_CONFLICT, UNSET)
    for r in await _results(db, project, target, fresh=False):
        if r.check.status == "fail":
            raise HTTPException(status.HTTP_409_CONFLICT, r.check.detail)


async def gate(
    db: AsyncSession, project_id: UUID, user: User, *, analysis: bool = False,
) -> None:
    """영상 등록·분석 요청을 처리하기 전에 권한·저장소 대상·분석 대상을 확인한다.

    권한을 먼저 확인한다. 권한 확인 전에 점검을 돌리면 관리자가 아닌 사용자가 외부 호출과
    고정 기록을 일으킬 수 있다.
    분석 요청(`analysis`)이면 자격증명이 없을 때의 409 `analysis_not_configured`를 먼저 던진다.
    저장소 대상을 분석 대상보다 먼저 검사한다. 분석 대상 점검의 연동 저장소 비교가 그 버킷을 쓴다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found), 관리자가 아닌 경우(403),
            `project_storage.require_ready`나 `require_ready`가 거부하는 경우(409).
        pc.CredentialsMissingError: 분석 요청인데 쓸 자격증명이 없는 경우.
    """
    project = await db.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)
    if analysis:
        await pc.ensure_loaded(db, pc.analysis_target_of(project.settings))
        presets.require(presets.of_project(project.settings), analysis=True,
                        project_settings=project.settings)
    await project_storage.require_ready(db, project)
    await require_ready(db, project)


# ── 선택지 ──────────────────────────────────────────────────────────────────


def _dto(target: pc.AnalysisTarget | None) -> ProjectAnalysisStackDTO:
    """선택을 응답 형식으로 바꾼다. 연결 id가 UUID 형식이 아니면 비운다."""
    t = target or pc.AnalysisTarget()
    try:
        cid = UUID(t.connection_id) if t.connection_id else None
    except ValueError:
        cid = None
    return ProjectAnalysisStackDTO(
        analysis_connection_id=cid, maiu_workspace=t.workspace, maiu_project_id=t.project_id)


def _clean(value: str | None) -> str | None:
    """입력값의 앞뒤 공백을 지운다. 비었으면 `None`이다."""
    return (value or "").strip() or None


def _selectable(conn: Connection) -> bool:
    """자격증명이 있고 마지막 점검이 정상인 연결이면 참."""
    return conn.secret_ciphertext is not None and conn.last_status == _SELECTABLE_STATUS


async def _candidates(
    db: AsyncSession, org_id: UUID, *, keep: str | None = None,
) -> list[Connection]:
    """고를 수 있는 후보 연결을 이름 순으로 읽는다. 그 조직의 MI 연결이다.

    `keep`은 이미 저장한 연결 id다. 다른 조직의 연결이어도 목록에 넣어 화면이 저장한 값을
    이름으로 표시하게 한다. 새로 고르는 저장(`_verify`)은 `keep` 없이 호출해 그 조직의 연결만
    받는다.
    """
    cond = Connection.org_id == org_id
    if keep:
        cond = cond | (cast(Connection.id, String) == keep)
    return list((await db.scalars(
        select(Connection).where(cond, Connection.provider == pc.ANALYSIS_PROVIDER)
        .order_by(Connection.name)
    )).all())


def _browser(connection_id: str, workspace: str | None):
    """목록을 읽을 클라이언트를 만든다. 워크스페이스·MI 프로젝트를 고르기 전에도 쓸 수 있다.

    Raises:
        pc.CredentialsMissingError: 연결의 자격증명을 읽지 못한 경우.
    """
    return maiu_client.get_client(pc.AnalysisTarget(
        connection_id=connection_id, workspace=workspace or _PLACEHOLDER,
        project_id=_PLACEHOLDER))


def _project_options(rows: list[dict], legacy: str | None) -> list[MaiuProjectOption]:
    """MI 프로젝트 목록 응답을 선택지로 바꾼다."""
    out = []
    for p in rows:
        pid = str(p.get("projectId") or p.get("id") or "")
        if not pid:
            continue
        out.append(MaiuProjectOption(
            project_id=pid, name=str(p.get("name") or pid),
            is_base_project=bool(p.get("isBaseProject")),
            provisioned=bool(legacy) and pid == str(legacy),
        ))
    return out


async def choices(
    db: AsyncSession, project: Project, *,
    connection_id: UUID | None = None, workspace: str | None = None,
) -> AnalysisChoicesDTO:
    """현재 선택·잠금·선택지를 돌려준다. 외부 조회 실패는 사유 문장으로 싣는다.

    실패가 모두 일시 오류(`_transient`)면 `error_transient`를 참으로 둔다. 화면은 그 값이 참이면
    실패 대신 주의와 다시 불러오기를 표시한다. 분류 기준은 점검(`_failure`)과 같다.

    `connection_id`로 고른 연결의 키로 워크스페이스 목록을 읽는다. 연결을 주지 않으면 읽지 않는다.
    `workspace`를 주면 그 워크스페이스의 연동 저장소와 MI 프로젝트 목록도 읽는다.
    분석 방식이 MI를 쓰지 않으면 외부 API를 호출하지 않는다.

    Raises:
        HTTPException: 고를 수 없는 연결로 목록을 읽으려는 경우(422 connection_not_found).
    """
    await pc.refresh(db)
    target = pc.analysis_target_of(project.settings)
    lock = await lock_state(db, project.project_id)
    conns = await _candidates(db, project.org_id,
                              keep=target.connection_id if target else None)
    out = AnalysisChoicesDTO(
        selected=_dto(target),
        supported=supports(project.settings),
        required=required(project.settings),
        locked=lock.locked,
        video_count=lock.videos,
        deleted_video_count=lock.deleted,
        legacy_maiu_project_id=(
            str((project.settings or {}).get(provisioning.LEGACY_SETTINGS_KEY) or "") or None),
        project_bucket=pc.project_bucket(project.settings) or None,
        connections=[
            AnalysisConnectionOption(
                id=c.id, name=c.name, has_secret=c.secret_ciphertext is not None,
                last_status=c.last_status, selectable=_selectable(c))
            for c in conns
        ],
        browse_connection_id=connection_id,
        browse_workspace=_clean(workspace),
        selected_project_name=pc.analysis_project_name_of(project.settings),
    )
    if connection_id is not None and all(c.id != connection_id for c in conns):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "connection_not_found")
    if not out.supported or connection_id is None:
        return out
    cid = str(connection_id)
    ws_name = out.browse_workspace
    try:
        cli = _browser(cid, ws_name)
    except pc.CredentialsMissingError:
        out.error = "분석 연결의 자격증명을 읽지 못했습니다"
        return out
    # 목록 조회와 워크스페이스 조회는 다른 API라 한쪽이 실패해도 다른 쪽은 성공할 수 있다.
    # 그래서 목록을 읽지 못해도 고른 워크스페이스는 따로 읽는다.
    errors: list[str] = []
    transient: list[bool] = []
    try:
        rows = await cli.list_workspaces()
    except Exception as e:  # noqa: BLE001
        log.warning("MI 워크스페이스 목록 조회 실패: %s %s", type(e).__name__, e)
        errors.append(f"워크스페이스 목록을 읽지 못했습니다 · {exception_reason(e)}")
        transient.append(_transient(e))
    else:
        out.workspaces = [
            MaiuWorkspaceOption(name=str(w.get("workspaceName") or w.get("name")),
                                bucket=w.get("bucketName") or None)
            for w in rows if w.get("workspaceName") or w.get("name")
        ]
    if ws_name:
        try:
            ws = provisioning.unwrap(await cli.get_workspace())
            out.workspace_bucket = ws.get("bucketName") or None
            out.projects = _project_options(
                await cli.list_projects(), out.legacy_maiu_project_id)
        except Exception as e:  # noqa: BLE001
            log.warning("MI 워크스페이스 조회 실패: %s %s", type(e).__name__, e)
            errors.append(
                "워크스페이스를 찾지 못했습니다" if provisioning.is_not_found(e)
                else f"워크스페이스를 읽지 못했습니다 · {exception_reason(e)}")
            transient.append(_transient(e))
    if ws_name and out.projects is not None:
        await _remember_name(db, project, target, cid, ws_name, out)
    out.error = " / ".join(errors) or None
    out.error_transient = bool(transient) and all(transient)
    return out


async def _remember_name(
    db: AsyncSession, project: Project, target: pc.AnalysisTarget | None,
    cid: str | None, ws_name: str, out: AnalysisChoicesDTO,
) -> None:
    """저장된 MI 프로젝트의 이름을 방금 읽은 목록에서 찾아 기록한다.

    이름 없이 저장된 선택(기본값 고정 기록 등)도 목록을 한 번 읽으면 이름이 기록된다.
    잠긴 프로젝트는 다시 저장할 수 없어 이 경로로만 이름이 기록된다.
    이름 키 하나만 DB에서 병합한다. 그 사이 다른 저장이 MI 프로젝트를 바꿨으면 WHERE 조건이
    맞지 않아 쓰지 않는다.
    """
    if target is None or not target.project_id:
        return
    if (cid, ws_name) != (target.connection_id, target.workspace):
        return
    name = next((o.name for o in out.projects or [] if o.project_id == target.project_id), None)
    if not name or name == out.selected_project_name:
        return
    out.selected_project_name = name
    await db.execute(
        text(
            "UPDATE projects SET settings = jsonb_set(settings, "
            "ARRAY[CAST(:key AS text), CAST(:nkey AS text)], to_jsonb(CAST(:name AS text))) "
            "WHERE project_id = :pid AND settings->:key->>:pkey = :mi"
        ),
        {"key": pc.STACK_KEY, "nkey": pc.STACK_MAIU_PROJECT_NAME, "pkey": pc.STACK_MAIU_PROJECT,
         "name": name, "mi": target.project_id, "pid": project.project_id},
    )
    await db.commit()


# ── 저장 ─────────────────────────────────────────────────────────────────────


async def _verify(db: AsyncSession, project: Project, target: pc.AnalysisTarget) -> str | None:
    """저장하려는 선택이 규칙에 맞는지 외부 점검까지 포함해 확인한다.

    Returns:
        점검이 확인한 MI 프로젝트 이름. 확인하지 못했으면 `None`이다.

    Raises:
        HTTPException: 연결이 후보가 아닌 경우(422 connection_not_found),
            연결의 서비스가 다른 경우(422 connection_provider_mismatch),
            연결의 마지막 점검이 정상이 아닌 경우(422 connection_not_verified),
            자격증명을 읽지 못한 경우(409 analysis_not_configured),
            점검이 실패한 경우(422 maiu_bucket_mismatch·maiu_project_not_found·
            maiu_target_not_found·maiu_access_denied),
            분석 서비스를 지금 읽지 못한 경우(503 maiu_unreachable).
    """
    await pc.refresh(db)
    conn = await db.get(Connection, UUID(target.connection_id or ""))
    if conn is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "connection_not_found")
    if conn.provider != pc.ANALYSIS_PROVIDER:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "connection_provider_mismatch")
    if conn.id not in {c.id for c in await _candidates(db, project.org_id)}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "connection_not_found")
    if not _selectable(conn):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "connection_not_verified")
    results = await _results(db, project, target, fresh=True)
    for r in results:
        if r.check.status not in ("fail", "warn") or r.code is None:
            continue
        if r.code == pc.ANALYSIS_NOT_CONFIGURED:
            raise HTTPException(status.HTTP_409_CONFLICT, r.code)
        if r.code == "maiu_unreachable":
            # 확인하지 못한 선택은 저장을 거부한다. 저장한 뒤 첫 등록에서 거부되면 원인을
            # 찾기 어렵다.
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, r.code)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, r.code)
    return next((r.name for r in results if r.name), None)


async def save(
    db: AsyncSession, project: Project, body: ProjectAnalysisStackDTO, actor: User,
) -> ProjectAnalysisStackDTO:
    """프로젝트의 분석 대상을 저장한다. 세 값을 모두 비우면 기록을 지운다.

    연결·워크스페이스·MI 프로젝트는 함께 고른다. 같은 값을 다시 보내면 아무것도 바꾸지 않는다.
    바꾼 값은 `project.updated` 감사 기록의 `changed.analysis_stack`에 남긴다.
    점검이 끝난 뒤 프로젝트 행을 잠그고 영상 수를 다시 센다(`stack_lock.lock_for_change`).
    점검하는 동안 첫 영상이 생겼으면 409로 거부한다.

    Raises:
        HTTPException: MI를 쓰지 않는 분석 방식인 경우(422 analysis_stack_unsupported),
            세 값 중 하나라도 비운 경우(422 analysis_stack_incomplete),
            영상이 있는 프로젝트의 선택을 바꾸는 경우(409 analysis_stack_locked),
            그 밖에 `_verify`가 거부하는 경우.
    """
    require_supported(project.settings)
    new = pc.AnalysisTarget(
        connection_id=str(body.analysis_connection_id) if body.analysis_connection_id else None,
        workspace=_clean(body.maiu_workspace), project_id=_clean(body.maiu_project_id))
    if not new.empty and not new.complete:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "analysis_stack_incomplete")
    current = pc.analysis_target_of(project.settings)
    after = None if new.empty else new
    if (current or pc.AnalysisTarget()) == (after or pc.AnalysisTarget()):
        return _dto(current)
    require_unlocked_or_same(current, after, await lock_state(db, project.project_id))
    name = await _verify(db, project, after) if after is not None else None
    lock = await lock_for_change(db, project)
    current = pc.analysis_target_of(project.settings)
    if (current or pc.AnalysisTarget()) == (after or pc.AnalysisTarget()):
        await db.commit()
        return _dto(current)
    if lock.locked:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, LOCKED)
    # 점검이 확인한 이름을 함께 기록한다. 그래서 MI 목록을 읽지 못하는 동안에도 화면과 진단이
    # 이름을 표시한다.
    project.settings = pc.with_analysis_project_name(
        pc.with_analysis_target(project.settings, after), name)
    audit.emit(
        db, event_type="project.updated", target_kind="project",
        target_id=project.project_id, actor_user_id=actor.user_id,
        project_id=project.project_id,
        payload={"changed": {"analysis_stack": {
            "from": current.as_dict() if current else None,
            "to": after.as_dict() if after else None,
        }}},
    )
    await db.commit()
    return _dto(after)


async def save_storage(
    db: AsyncSession, project: Project, body: ProjectStorageStackDTO, actor: User,
    *, origin: str | None = None,
) -> ProjectStorageStackDTO:
    """프로젝트의 저장소 대상을 저장한다. 분석 대상이 있으면 그 워크스페이스의 연동 버킷과 비교한다.

    MI 워크스페이스 하나는 연동 버킷 하나만 읽는다. 저장한 분석 대상의 연동 버킷과 다른 버킷을
    고르면 분석 요청이 거절되므로 422로 거부한다. 둘을 함께 바꾸려면 분석 대상을 먼저 비운다.
    연동 버킷을 지금 읽지 못하면 저장을 허용한다. 분석 대상 점검이 같은 비교를 다시 한다.
    잠금은 버킷 비교보다 먼저 검사한다. 잠긴 프로젝트는 버킷을 맞춰도 바꿀 수 없다.

    Raises:
        HTTPException: 영상이 있는 프로젝트의 선택을 바꾸는 경우(409 storage_stack_locked),
            저장한 분석 대상의 워크스페이스 연동 버킷이 다른 경우(422 maiu_bucket_mismatch),
            그 밖에 `project_storage.save`가 거부하는 경우.
    """
    new = project_storage.target_from(body)
    if project_storage.supports(project.settings):
        await project_storage.require_unlocked(db, project, new)
    current = pc.storage_target_of(project.settings)
    analysis = pc.analysis_target_of(project.settings)
    if (new is not None and new.bucket and new != current and supports(project.settings)
            and analysis is not None and analysis.project_id):
        await pc.ensure_loaded(db, analysis)
        try:
            ws = provisioning.unwrap(await maiu_client.get_client(analysis).get_workspace())
            linked = str(ws.get("bucketName") or "") or None
        except Exception as e:  # noqa: BLE001 — 읽지 못한 것은 분석 대상 점검이 다시 본다
            log.warning("저장소 저장 중 연동 버킷 조회 실패: %s", type(e).__name__)
            linked = None
        if linked is not None and linked != new.bucket:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "maiu_bucket_mismatch")
    return await project_storage.save(db, project, body, actor, origin=origin)


# ── 주기 점검 ────────────────────────────────────────────────────────────────


#: 주기 점검 종류별 상태 변화 이벤트. 두 점검은 따로 기록한다.
_KINDS: Final = {
    "analysis": HEALTH_EVENT,
    "storage": project_storage.HEALTH_EVENT,
}


async def _last(db: AsyncSession, project_id: UUID, kind: str = "analysis") -> DomainEvent | None:
    """프로젝트의 마지막 점검 상태 변화 기록을 읽는다."""
    return await db.scalar(
        select(DomainEvent)
        .where(DomainEvent.event_type == _KINDS[kind], DomainEvent.target_id == project_id)
        .order_by(DomainEvent.occurred_at.desc()).limit(1))


async def record(
    db: AsyncSession, project: Project, checks: list[Check], *, kind: str = "analysis",
) -> bool:
    """주기 점검 결과를 기록한다. 상태가 바뀐 경우에만 전이 기록을 남긴다. commit은 호출부가 한다.

    판정 규칙은 연결 점검(`connections.record_result`)과 같다.
    `warn`(일시 오류·CORS)은 정상으로 센다.
    그래서 점검 주기마다 바뀌는 일시 오류가 전이로 쌓이지 않는다.
    `kind`는 `analysis`(분석 대상)나 `storage`(저장소 대상)다.

    Returns:
        상태가 바뀌었으면 참.
    """
    event = _KINDS[kind]
    failed = [c for c in checks if c.status == "fail"]
    after = "fail" if failed else "ok"
    last = await _last(db, project.project_id, kind)
    before = (last.payload or {}).get("after") if last else None
    detail = failed[0].detail if failed else "정상"
    changed = before != after
    if changed:
        audit.emit(
            db, event_type=event, target_kind="project", target_id=project.project_id,
            project_id=project.project_id,
            payload={"before": before, "after": after, "detail": detail}, kind="audit",
        )
    return changed


async def _recheck(db: AsyncSession, column, kind: str, supported, run) -> int:
    """기록이 있는 프로젝트를 하나씩 점검하고 결과를 기록한다.

    한 프로젝트의 점검이 실패하면 그 건만 롤백하고 나머지를 계속 점검한다.

    Args:
        column: 기록 여부를 판정하는 `stack` 블록 안의 키.
        supported: 프로젝트 설정을 받아 그 점검 대상인지 판정하는 함수.
        run: 프로젝트를 받아 점검 결과를 돌려주는 코루틴 함수.

    Returns:
        상태가 바뀐 프로젝트 수.
    """
    pids = list((await db.scalars(
        select(Project.project_id).where(
            Project.deleted_at.is_(None),
            func.coalesce(Project.settings[pc.STACK_KEY][column].astext, "") != "",
        )
    )).all())
    changed = 0
    for pid in pids:
        project = await db.get(Project, pid)
        if project is None or not supported(project.settings):
            continue
        try:
            if await record(db, project, await run(project), kind=kind):
                changed += 1
            await db.commit()
        except Exception as e:  # noqa: BLE001
            log.warning("%s 주기 점검 실패 project=%s: %s", _KINDS[kind][1], pid,
                        type(e).__name__, exc_info=True)
            await db.rollback()
    return changed


async def recheck_all(db: AsyncSession) -> int:
    """저장소 대상과 분석 대상을 기록한 프로젝트를 모두 점검한다. 연결 주기 점검 태스크가 호출한다.

    저장소를 먼저 점검한다. 분석 대상은 분석 연동이 켜진 서버에서만 점검한다. 연동이 꺼진 서버는
    가짜 클라이언트가 응답하므로 점검할 외부 서비스가 없다. 주기 점검에는 화면 주소(origin) 정보가
    없으므로 CORS는 업로드 방법을 허용하는 규칙이 있는지만 확인한다.

    Returns:
        상태가 바뀐 (프로젝트, 점검 종류) 수. 0은 정상 결과다.
    """
    await pc.refresh(db)
    changed = await _recheck(
        db, pc.STACK_BUCKET, "storage", project_storage.supports,
        lambda project: project_storage.check(db, project, fresh=True))
    if settings.maiu_enabled:
        changed += await _recheck(
            db, pc.STACK_MAIU_PROJECT, "analysis", supports,
            lambda project: check(db, project, fresh=True))
    return changed
