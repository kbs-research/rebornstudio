"""검수 대기 수, 다음 검수, 판정을 다루는 검수 API 라우터."""
from uuid import UUID

from fastapi import APIRouter, Depends
from reborn_contracts import (
    NextWorkResponse,
    ReviewDecisionRequest,
    WorkItemDTO,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import User
from api_gateway.services.work import review as review_service

router = APIRouter()


@router.get(
    "/projects/{project_id}/review/available-count",
    response_model=dict[str, int],
    summary="지금 배정할 수 있는 검수 작업 수 + 내 보유 수",
    tags=["review"],
)
async def available_count(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, int]:
    """지금 배정할 수 있는 검수 작업 수(`count`)와 호출한 사용자의 검수 보유 수(`held`)를 돌려준다.

    다음 검수와 같은 조건으로 수만 센다. 목록으로 세면 다른 사람이 점유한 검수까지 대기로 잡혀
    다음 검수가 404를 낸다.
    """
    return await review_service.claimable_summary(db, project_id, user)


@router.patch(
    "/work/{work_item_id}/review/draft",
    response_model=WorkItemDTO,
    summary="검수 초안 저장 (내가 claim한 건만)",
    tags=["review"],
)
async def save_review_draft(
    work_item_id: UUID,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """지적 문장·반려 사유·수정 후 승인 캡션을 판정 전까지 서버에 임시 저장한다.

    호출한 사용자가 점유(claim)한 작업에만 저장할 수 있다. 초안은 판정할 때까지 유지된다.
    탭이 닫히거나 토큰이 만료돼도 작성한 내용이 남는다.
    """
    return await review_service.save_draft(db, work_item_id, user, payload)


@router.post(
    "/projects/{project_id}/review/next",
    response_model=NextWorkResponse,
    summary="다음 검수 작업 한 건 (보유한 검수가 있으면 그 작업, reviewer)",
    tags=["review"],
)
async def next_review(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """호출한 사용자의 다음 검수 작업을 돌려준다.

    보유한 검수가 있으면 그 작업을 다시 주고(`resumed`), 없으면 제출 순으로 한 건을 배정한다.
    보유는 판정하거나 비식별화 미흡을 신고할 때까지 이어진다.
    """
    return await review_service.next_review(db, project_id, user)


@router.get(
    "/projects/{project_id}/review/mine",
    response_model=list[WorkItemDTO],
    summary="내가 보유한 검수 작업 (내 작업·워크벤치)",
    tags=["review"],
)
async def list_my_review(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """호출한 사용자가 보유한 submitted work_item 목록을 다음 검수가 여는 순서로 돌려준다.

    검수의 `내 작업`과 워크벤치가 쓴다.
    """
    return await review_service.list_my_review(db, project_id, user)


@router.post(
    "/work/{work_item_id}/review",
    response_model=WorkItemDTO,
    summary="검수 판정 (승인·반려·수정 후 승인, 반려는 만든 사람에게 재작업으로 돌아감)",
    tags=["review"],
)
async def decide(
    work_item_id: UUID,
    payload: ReviewDecisionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """work_item을 승인·반려·수정 후 승인 중 하나로 판정한다.

    반려하면 원 작업자의 재작업으로 돌린다.
    """
    return await review_service.decide(db, work_item_id, payload, user)
