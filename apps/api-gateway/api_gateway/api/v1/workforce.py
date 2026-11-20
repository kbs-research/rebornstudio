"""본인 판정 목록·로그인 이력·작업 이력과 프로젝트 나가기를 다루는 작업자 운영 API 라우터.

모든 경로는 호출한 사용자 본인의 기록만 돌려준다.
"""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from reborn_contracts import (
    DomainEventDTO,
    LeaveProjectRequest,
    LeaveProjectResponse,
    MemberDecisionPage,
    MyWorkHistoryDetail,
    MyWorkHistoryRow,
    MyWorkState,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import User
from api_gateway.services.org import project as project_service
from api_gateway.services.work import service as work_service
from api_gateway.services.workforce import decisions as decisions_service
from api_gateway.services.workforce import login_history as login_history_service

router = APIRouter(tags=["workforce"])


@router.get(
    "/projects/{project_id}/me/decisions",
    response_model=MemberDecisionPage,
    summary="내가 내린 판정 목록 (멤버 본인)",
)
async def my_decisions(
    project_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    before: datetime | None = Query(
        default=None, description="cursor: 앞 페이지 마지막 행의 decided_at"
    ),
    before_id: UUID | None = Query(
        default=None, description="cursor: 앞 페이지 마지막 행의 decision_id"
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """본인이 이 프로젝트에서 내린 검수 판정을 최신순 커서 페이지로 돌려준다.

    판정 목록과 전체 수만 싣고 비율·소요 시간 같은 지표는 싣지 않는다.
    """
    return await decisions_service.my_decisions(
        db, project_id, user, limit=limit, before_at=before, before_id=before_id,
    )


@router.get(
    "/me/auth-events",
    response_model=list[DomainEventDTO],
    summary="본인 로그인/로그아웃 이력",
)
async def my_auth_events(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """본인의 최근 로그인·로그인 실패·로그인 잠금·로그아웃 이벤트를 최신순으로 돌려준다."""
    return await login_history_service.my_auth_events(db, user, limit=limit)


@router.get(
    "/projects/{project_id}/me/work-history",
    response_model=list[MyWorkHistoryRow],
    summary="작업자 본인 작업 이력 (active 멤버)",
)
async def my_work_history(
    project_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = Query(
        default=None, description="cursor: 앞 페이지 마지막 행의 history_at"
    ),
    before_id: UUID | None = Query(default=None, description="cursor: 마지막 work_item_id"),
    q: str | None = Query(
        default=None, max_length=64, description="작업 ID 앞자리 또는 영상 제목의 일부"
    ),
    state: MyWorkState | None = Query(default=None, description="이 상태의 행만 싣는다"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """작업자 본인의 작업 이력을 최신순 커서 페이지로 돌려준다. 프로젝트 활성 멤버가 부를 수 있다.

    작업자가 자기 기록을 확인하게 하는 투명성 기능이다. 다른 사람의 개인정보와 입력은 싣지 않는다.
    행마다 호출한 사용자의 제출 시각·작업 시간·상태(`my_*`)를 싣는다. 작업이 다른 라벨러에게
    넘어가도 호출한 사용자의 값이 남는다. 알 수 없는 `state`는 422를 돌려준다.
    """
    return await work_service.list_my_history(
        db, project_id, user, limit=limit, before_at=before, before_id=before_id, q_text=q,
        state=state,
    )


@router.get(
    "/projects/{project_id}/me/work-history/{work_item_id}",
    response_model=MyWorkHistoryDetail,
    summary="작업자 본인 작업 이력 상세",
)
async def my_work_history_detail(
    project_id: UUID,
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """호출한 사용자의 작업 이력 한 건을 상세히 돌려준다.

    호출한 사용자가 낸 라벨과 그 라벨의 검수만 싣는다. 작업이 다른 라벨러에게 넘어간 뒤에도
    조회할 수 있다. 호출한 사용자의 이력에 없는 작업은 404 `work_item_not_found`를 돌려준다.
    """
    return await work_service.my_history_detail(db, project_id, work_item_id, user)


@router.post(
    "/projects/{project_id}/me/leave",
    response_model=LeaveProjectResponse,
    summary="프로젝트 나가기 — 조기 이탈(claimed 반환, active 멤버)",
)
async def leave_project(
    project_id: UUID,
    payload: LeaveProjectRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """작업자가 스스로 프로젝트에서 나간다.

    진행 중인 작업은 큐로 돌아간다.
    """
    return await project_service.leave_project(db, project_id, user, payload.reason)
