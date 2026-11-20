"""관리자 API 라우터. 모두 super_admin 전용이다."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from reborn_contracts import (
    DiagnosticsReport,
    DomainEventDTO,
    PasswordResetIssued,
    UserCreated,
    UserCreateRequest,
    UserDTO,
    UserRoleUpdate,
    UserStatusUpdate,
)
from reborn_contracts.connection import ConnectionHealthSummary
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_db, require_super_admin
from api_gateway.models import User
from api_gateway.services.auth import admin as admin_service
from api_gateway.services.ops import audit_log as audit_log_service
from api_gateway.services.ops import diagnostics as diag_service
from api_gateway.services.stacks import connections as connections_service

router = APIRouter()


@router.post(
    "/users",
    response_model=UserCreated,
    status_code=status.HTTP_201_CREATED,
    summary="계정 생성 (super_admin)",
)
async def create_user(
    payload: UserCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """활성 계정을 만들고 비밀번호를 정할 1회용 재설정 링크 토큰을 돌려준다.

    super_admin만 부를 수 있다.
    """
    return await admin_service.create_user(db, payload, admin)


@router.patch(
    "/users/{user_id}/role",
    response_model=UserDTO,
    summary="사용자 역할 변경 (super_admin)",
)
async def set_user_role(
    user_id: UUID,
    payload: UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """사용자의 `global_role`을 바꾼다. super_admin만 부를 수 있고, 자기 역할은 바꿀 수 없다."""
    return await admin_service.set_role(db, user_id, admin, payload.role)


@router.patch(
    "/users/{user_id}/status",
    response_model=UserDTO,
    summary="사용자 상태 변경 (super_admin)",
)
async def set_user_status(
    user_id: UUID,
    payload: UserStatusUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """사용자를 active 또는 suspended로 바꾼다. super_admin만 부를 수 있다.

    자기 계정은 정지할 수 없다.
    """
    return await admin_service.set_status(db, user_id, admin, payload.status)


@router.post(
    "/users/{user_id}/password-reset",
    response_model=PasswordResetIssued,
    summary="비밀번호 재설정 링크 발급 (super_admin)",
)
async def issue_password_reset(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """사용자의 1회용 비밀번호 재설정 토큰을 발급한다. super_admin만 부를 수 있다.

    토큰은 이 응답에만 싣는다. 서버는 해시만 저장하므로 다시 조회할 수 없다.
    새로 발급하면 그 사용자에게 먼저 발급한 링크는 무효가 된다.
    """
    return await admin_service.issue_password_reset(db, user_id, admin)


@router.get(
    "/diagnostics/last",
    response_model=DiagnosticsReport | None,
    summary="마지막 연동 점검 결과 — 외부 요청 없음 (super_admin)",
)
async def diagnostics_last(
    request: Request,
    _: User = Depends(require_super_admin),
) -> DiagnosticsReport | None:
    """15분 주기 점검(`check_integrations`)이 남긴 마지막 결과를 읽는다.

    super_admin만 부를 수 있다. 외부에 요청하지 않으므로 화면이 열릴 때 불러도 된다.
    아직 한 번도 점검하지 않았으면 `null`이다. 화면은 `null`을 정상과 구분해 표시한다.
    주기 점검 결과를 돌려주므로 아무도 진단을 실행하지 않아도 만료된 자격증명이 화면에 나타난다.
    요청이 있어야 판정하는 항목(클라이언트 주소·TLS)은 이 요청으로 다시 판정해 싣는다
    (`with_request_checks`). 주기 점검에는 요청이 없어 그 항목을 볼 수 없다.
    """
    snap = diag_service.last_snapshot()
    if snap is None:
        return None
    return DiagnosticsReport(
        checks=diag_service.with_request_checks(snap.get("checks", []), request),
        failed=snap.get("failed", 0),
        checked_at=snap.get("checked_at"),
    )


@router.get(
    "/connections/health",
    response_model=ConnectionHealthSummary,
    summary="모든 조직 자격증명 연결의 마지막 점검 집계 — 외부 요청 없음 (super_admin)",
)
async def connections_health(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
) -> ConnectionHealthSummary:
    """플랫폼 개요의 "자격증명 연결" 줄에 쓸 집계를 돌려준다. super_admin만 부를 수 있다.

    연동 점검(`.env`)과 분모가 달라 따로 돌려준다.
    `.env` 기준 연동 점검만 세면 자격증명 연결이 실패해도 개요가 "모두 정상"으로 표시한다.
    각 연결에 남은 마지막 점검 결과만 세고 외부에 요청하지 않으므로 화면이 열릴 때 불러도 된다.
    """
    return await connections_service.health_summary(db)


# ── 사용자 활동 이력 ──


@router.get(
    "/users/{user_id}/activity",
    response_model=list[DomainEventDTO],
    summary="사용자 활동 이력 (super_admin)",
)
async def user_activity(
    user_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    include_session_refresh: bool = Query(
        default=False,
        description="세션 갱신(auth.refreshed)도 포함할지. 기본은 뺀다",
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    """특정 사용자의 domain_events를 최신순으로 돌려준다. super_admin만 부를 수 있다.

    세션 갱신은 기본으로 뺀다.
    세션 갱신은 활동 중이면 시간마다 한 행씩 쌓인다. 포함하면 상한 100행의 대부분이
    `인증 세션 갱신`이 되어 이 사람이 한 일이 가려진다.
    """
    return await audit_log_service.user_activity(
        db, user_id, limit=limit, include_session_refresh=include_session_refresh,
    )
