"""프로젝트 지표를 다루는 대시보드 API 라우터.

라벨러 실적, 사업 KPI 등을 돌려준다.
"""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from reborn_contracts import (
    BusinessKpi,
    CapacityHealth,
    LabelerPerfRow,
    LabelerPerfSummary,
    PipelineSummary,
    WorkflowHealth,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import User
from api_gateway.services.analytics import dashboard as dashboard_service

router = APIRouter()


@router.get(
    "/projects/{project_id}/dashboard/labeler-performance",
    response_model=list[LabelerPerfRow],
    summary="사람별 라벨링 실적 (관리자)",
    tags=["dashboard"],
)
async def labeler_performance(
    project_id: UUID,
    since: datetime | None = Query(
        None,
        description="이 시각 이후(포함)의 활동만 센다. 없으면 전 기간 누적.",
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """사람별 작업량·반려·최근 활동을 제출 수가 많은 순으로 돌려준다.

    관리자가 부를 수 있다.
    행은 활성 멤버 전원과, 멤버가 아니지만 기간 안에 이 프로젝트에 제출한 사람이다.
    멤버가 아닌 사람의 `role`은 `null`이다.
    `since`를 주지 않으면 전체 기간 누적이다.
    `최근 활동`은 기간과 관계없이 마지막으로 활동한 시각이다.
    """
    return await dashboard_service.labeler_performance(db, project_id, user, since=since)


@router.get(
    "/projects/{project_id}/dashboard/labeler-performance/summary",
    response_model=LabelerPerfSummary,
    summary="기간 안의 프로젝트 라벨링 합계 (관리자)",
    tags=["dashboard"],
)
async def labeler_summary(
    project_id: UUID,
    since: datetime | None = Query(
        None,
        description="이 시각 이후(포함)의 활동만 센다. 없으면 전 기간 누적.",
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """기간 안의 제출·판정 합계와 승인율·반려율·수정률을 돌려준다.

    관리자가 부를 수 있다.
    수는 같은 `since`의 사람별 실적 행을 모두 더한 값이다.
    비율은 그 합계에 사람별 행과 같은 규칙을 적용한다.
    """
    return await dashboard_service.labeler_summary(db, project_id, user, since=since)


@router.get(
    "/projects/{project_id}/dashboard/capacity",
    response_model=CapacityHealth,
    summary="처리 능력 — 임계 초과·납품 불가 사유 (관리자)",
    tags=["dashboard"],
)
async def capacity_health(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """임계 일수를 넘긴 작업 수와 납품 불가 사유를 돌려준다. 관리자가 부를 수 있다."""
    return await dashboard_service.capacity_health(db, project_id, user)


@router.get(
    "/projects/{project_id}/dashboard/workflow-health",
    response_model=WorkflowHealth,
    summary="워크플로 건강 (관리자)",
    tags=["dashboard"],
)
async def workflow_health(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """상태별 작업 수(큐 깊이)를 돌려준다. 관리자가 부를 수 있다."""
    return await dashboard_service.workflow_health(db, project_id, user)


@router.get(
    "/projects/{project_id}/pipeline/summary",
    response_model=PipelineSummary,
    summary="작업 단계별 대기 건수 (스텝퍼·사이드바 배지, 관리자)",
    tags=["dashboard"],
)
async def pipeline_summary(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """비식별화 신고·분석·변환·라벨링·검수의 대기 건수와 반복 반려·완료 수를 한 번에 돌려준다.

    관리자가 부를 수 있다.
    """
    return await dashboard_service.pipeline_summary(db, project_id, user)


@router.get(
    "/projects/{project_id}/dashboard/business-kpi",
    response_model=BusinessKpi,
    summary="사업 KPI (관리자·검수자)",
    tags=["dashboard"],
)
async def business_kpi(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """건수·시간 진척률과 계약 목표·마감을 돌려준다. 관리자 응답에는 납품 가능 수를 싣는다.

    작업 결과를 볼 수 있는 관리자와 검수자가 부를 수 있다.
    """
    return await dashboard_service.business_kpi(db, project_id, user)
