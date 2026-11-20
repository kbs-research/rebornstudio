"""MI 분석 자원의 읽기 전용 진단.

분석 요청이 실제로 돌려면 세 가지가 맞아야 한다.
프로젝트가 고른 워크스페이스가 있어야 하고, 그 워크스페이스의 연동 버킷이 프로젝트 저장소 버킷과
같아야 하고, 분석 프로젝트를 골라 저장해야 한다.
두 버킷이 다르면 등록과 업로드는 모두 성공하고 분석만 알림 없이 실패한다.
그래서 진단이 두 버킷의 차이를 보여 준다.

워크스페이스·버킷 연동·분석 프로젝트는 운영자가 NCP 콘솔에서 미리 만든다(`docs/install.md`).
이 모듈은 벤더 계정에 자원을 만들지 않고 읽기 호출만 한다. 그래서 화면이 자동으로 불러도 안전하다.
고른 값은 프로젝트 설정의 `stack` 블록(`platform_credentials.AnalysisTarget`)에 있고,
저장은 분석 대상 저장 점검(`stacks.project_stack.save`)이 맡는다.
"""
from __future__ import annotations

import logging
import re

from api_gateway.config import settings
from api_gateway.services import platform_credentials
from api_gateway.services.ops.diagnostics import (
    Check,
    credential_hint,
    exception_reason,
    missing_detail,
)

log = logging.getLogger(__name__)

#: 옛 설정이 MI 프로젝트 id를 기록한 설정 최상위 키. 라우팅에는 쓰지 않는다.
#: 설정 화면은 이 값을 이 프로젝트용 MI 프로젝트로 표시하고 고를 수 있게 한다.
LEGACY_SETTINGS_KEY = "maiu_project_id"


def _hint(slot: str) -> str:
    """프로젝트 화면에 표시할 자격증명 안내를 만든다. 고칠 곳은 분석 연동과 자격증명 연결이다."""
    return credential_hint(slot)


def _unreadable(e: BaseException) -> Check:
    """워크스페이스를 읽지 못했을 때의 진단 항목을 만든다. 일시 오류는 실패 대신 주의로 표시한다.

    요청량 제한·서버 오류·응답 지연은 다시 점검하면 풀린다.
    실패로 표시하면 운영자가 자격증명을 의심해 맞는 값을 고치게 된다.
    """
    from api_gateway.services.maiu.naver_client import MaiuTransientError

    if isinstance(e, MaiuTransientError):
        return Check("maiu_ws", "분석 워크스페이스", "warn",
                     f"지금 확인하지 못했습니다 · {exception_reason(e)}",
                     "잠시 후 다시 점검합니다.")
    return Check("maiu_ws", "분석 워크스페이스", "fail", f"조회 실패 · {exception_reason(e)}",
                 f"인증 정보 문제일 수 있습니다. {_hint('analysis')}")


def _missing_analysis() -> Check:
    """분석 연결을 고르지 않았거나 고른 연결을 읽지 못했을 때의 진단 항목을 만든다."""
    return Check("maiu_ws", "분석 워크스페이스", "fail", missing_detail("analysis"),
                 _hint("analysis"))


#: 워크스페이스 연동 버킷이 프로젝트 저장소와 달라 생성을 거절할 때의 오류 코드.
BUCKET_MISMATCH = "maiu_bucket_mismatch"


def bucket_matches(linked: str | None, mine: str | None) -> bool:
    """워크스페이스 연동 버킷이 프로젝트가 쓰는 버킷과 같으면 True. 연동이 없으면 False.

    MI 워크스페이스는 연동 버킷만 읽는다. 그래서 두 버킷이 다르면 등록과 업로드는 성공하고
    분석 요청만 거절된다.
    분석 대상 저장 점검이 이 함수로 진단과 같은 기준을 쓴다.
    """
    return bool(linked) and linked == mine


#: 워크스페이스 조회 실패가 대상 없음(404)인지 판별하는 패턴.
#: 실제 클라이언트의 예외 메시지에는 `MAIU GET … → 404 …`가, 가짜 클라이언트의 메시지에는
#: `not found`가 들어 있다.
_NOT_FOUND = re.compile(r"→\s*404\b|\bnot found\b", re.IGNORECASE)


def is_not_found(exc: BaseException) -> bool:
    """조회 실패의 원인이 대상 없음(404)이면 True. True일 때만 새로 만들어도 된다.

    자격증명·네트워크 실패면 False.
    """
    return bool(_NOT_FOUND.search(str(exc)))


def unwrap(resp: dict | None) -> dict:
    """MI 응답에서 `result`를 꺼낸다.

    MI 응답은 `{"code","message","result":{…}}`로 감싸여 온다.
    가짜 클라이언트는 감싸지 않은 형태를 돌려주므로 두 형태를 모두 받는다.
    주의: 테스트가 실제와 다른 응답 형태를 쓰면 그 차이만큼 테스트가 실제 동작을 검증하지 못한다.
    """
    return (resp or {}).get("result") or resp or {}


async def _project_name(cli, project_id: str, project_settings: dict | None) -> str | None:
    """진단 항목에 표시할 MI 프로젝트 이름을 찾는다. 기록한 이름이 없으면 목록에서 찾는다.

    이름은 표시용이라 목록을 읽지 못해도 진단을 실패로 만들지 않는다.
    그때는 `None`을 돌려주고 진단 항목에는 id만 표시한다.
    """
    recorded = platform_credentials.analysis_project_name_of(project_settings)
    if recorded:
        return recorded
    try:
        rows = await cli.list_projects()
    except Exception as e:  # noqa: BLE001 — 이름을 못 찾아도 진단은 이어 간다
        log.info("MI 프로젝트 이름 조회 실패: %s", type(e).__name__)
        return None
    for p in rows or []:
        if str(p.get("projectId") or p.get("id") or "") == str(project_id):
            return str(p.get("name") or "") or None
    return None


#: 분석 프로젝트를 고르기 전에 워크스페이스만 읽는 클라이언트에 넘기는 임시 프로젝트 id.
#: 워크스페이스 조회·목록·생성 경로는 이 값을 쓰지 않는다.
_PLACEHOLDER_PROJECT = "-"


def _workspace_client(target: platform_credentials.AnalysisTarget | None):
    """고른 연결·워크스페이스로 워크스페이스를 읽는 클라이언트를 만든다.

    진단은 분석 프로젝트를 고르기 전에도 워크스페이스를 읽으므로 분석 프로젝트는 비어 있어도 된다.
    연결과 워크스페이스는 반드시 골라야 한다.

    Raises:
        platform_credentials.CredentialsMissingError: 연결이나 워크스페이스를 고르지 않았거나
            고른 연결을 읽지 못한 경우.
    """
    from dataclasses import replace

    from api_gateway.services.maiu import client as maiu_client

    if target is None or not (target.connection_id and target.workspace):
        raise platform_credentials.CredentialsMissingError(
            platform_credentials.ANALYSIS_NOT_CONFIGURED)
    return maiu_client.get_client(
        replace(target, project_id=target.project_id or _PLACEHOLDER_PROJECT))


async def diagnose(project_settings: dict | None = None) -> list[Check]:
    """프로젝트가 고른 워크스페이스로 지금 분석을 실행할 수 있는지 읽기 호출만으로 확인한다.

    연동 진단과 같은 `Check` 형태를 돌려준다.
    두 화면이 형태를 따로 정하면 같은 사실이 화면마다 다르게 표시된다.
    프로젝트가 MI 연결·워크스페이스·MI 프로젝트를 골랐으면 그 값으로 확인한다.
    호출하는 쪽은 `platform_credentials.ensure_loaded`를 먼저 불러 고른 연결을 메모리에 올려 둔다.

    Returns:
        list[Check]: 워크스페이스, 연동 버킷, MI 프로젝트 세 항목.
    """
    if not settings.maiu_enabled:
        # 아래 문장은 화면(연동 진단)에 그대로 표시된다. 두 문장을 대시로 잇지 않는다.
        # 벤더 이름은 부연 설명에만 쓰고 항목 이름에는 쓰지 않는다.
        # 프로젝트 관리자가 보는 카드라서 서버 환경 변수 이름을 표시하지 않는다(`_hint`).
        return [Check("maiu_ws", "분석 워크스페이스", "warn",
                      "분석 연동이 꺼져 있습니다",
                      "실제 분석을 쓰려면 최고관리자가 서버에서 켜야 합니다.")]


    target = platform_credentials.analysis_target_of(project_settings)
    try:
        cli = _workspace_client(target)
    except platform_credentials.CredentialsMissingError:
        return [_missing_analysis()]
    # 워크스페이스 이름은 클라이언트의 값을 따른다.
    # 가짜 클라이언트에는 이 속성이 없어서 고른 값을 쓴다.
    want = getattr(cli, "workspace", None) or (target.workspace if target else None) or ""
    try:
        ws = unwrap(await cli.get_workspace())
    except Exception as e:  # noqa: BLE001
        # 워크스페이스가 없는 경우(404)와 읽지 못한 경우의 안내를 나눈다.
        # 404는 콘솔에서 만들면 해결되지만 자격증명 문제는 만들어도 해결되지 않는다.
        # 예외 클래스 이름 대신 진단 카드와 같은 사전(`exception_reason`)으로 문장을 만든다.
        if is_not_found(e):
            return [Check("maiu_ws", "분석 워크스페이스", "fail", "워크스페이스가 없습니다",
                          "NCP 콘솔에서 워크스페이스를 만들고 분석 대상에서 고릅니다.")]
        return [_unreadable(e)]

    checks = [Check("maiu_ws", "분석 워크스페이스", "ok", ws.get("workspaceName") or want)]

    linked = ws.get("bucketName")
    if not linked:
        checks.append(Check("maiu_bucket", "연결된 저장소", "fail",
                            "워크스페이스에 연결된 저장소가 없습니다",
                            "연결이 없으면 분석 요청이 거절되고 "
                            "분석 대상 점검에서 영상 등록도 막힙니다"))
    elif linked != platform_credentials.project_bucket(project_settings):
        checks.append(Check(
            "maiu_bucket", "연결된 저장소", "fail",
            f"분석 서버에 연결된 저장소는 {linked}인데 이 프로젝트가 쓰는 저장소는 "
            f"{platform_credentials.project_bucket(project_settings) or '없음'}입니다",
            "값이 다르면 분석 요청이 거절되고 분석 대상 점검에서 영상 등록도 막힙니다"))
    else:
        checks.append(Check("maiu_bucket", "연결된 저장소", "ok", f"{linked} 일치"))

    # 분석 요청이 실제로 읽는 값(`stack` 블록)으로 판정한다.
    # 분석 연동이 켜진 서버에서는 이 기록이 없으면 영상 등록과 분석 요청이 막힌다.
    # 설정 최상위의 옛 키(`LEGACY_SETTINGS_KEY`)는 라우팅에 쓰지 않는다.
    # 그래서 옛 키만 있는 프로젝트에는 그 MI 프로젝트를 골라 저장하라고 안내한다.
    chosen = target.project_id if target else None
    legacy = (project_settings or {}).get(LEGACY_SETTINGS_KEY)
    if chosen:
        name = await _project_name(cli, chosen, project_settings)
        checks.append(Check("maiu_project", "분석 프로젝트", "ok",
                            platform_credentials.analysis_project_label(name, chosen)))
    elif legacy:
        checks.append(Check("maiu_project", "분석 프로젝트", "warn",
                            "이 프로젝트의 분석 대상을 저장하지 않았습니다",
                            f"이 프로젝트용으로 만든 분석 프로젝트(id {legacy})가 있습니다. "
                            "분석 대상에서 고르고 저장합니다."))
    else:
        checks.append(Check("maiu_project", "분석 프로젝트", "warn",
                            "이 프로젝트의 분석 대상을 저장하지 않았습니다",
                            "분석 대상을 저장해야 영상을 등록하고 분석을 요청할 수 있습니다. "
                            "분석 프로젝트는 NCP 콘솔에서 만든 뒤 분석 대상에서 고릅니다."))
    return checks
