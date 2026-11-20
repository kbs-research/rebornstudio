"""분석 요청, 잡 목록, 비용 통계를 다루는 MI(Media Intelligence) API 라우터.

외부 벤더 식별자(MAIU, /maiu/, MaiuJobDTO)는 코드와 라우트에 그대로 둔다.
사용자에게 보이는 문구만 MI로 통일한다.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from reborn_contracts import MaiuDispatchRequest, MaiuDispatchResult, MaiuJobDTO
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import User
from api_gateway.services import authz
from api_gateway.services.maiu import service as maiu_service
from api_gateway.services.stacks import project_stack

router = APIRouter()


@router.post(
    "/projects/{project_id}/maiu/retry",
    summary="분석 실패 재시도 — 자산이 있으면 분석부터, 없으면 등록부터",
    tags=["maiu"],
)
async def retry_failed(
    project_id: UUID,
    payload: MaiuDispatchRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """실패한 MI 잡을 다시 요청한다. project_admin만 부를 수 있다.

    MI에 자산이 있으면 같은 영상이 두 번 올라가지 않도록 재등록 없이 분석만 다시 요청한다.
    자산이 없으면 등록부터 다시 한다.
    분석은 시간당 과금이라 자동으로 재시도하지 않고 사람이 실행한다. 권한도 dispatch와 같다.

    Raises:
        HTTPException: project_admin이 아닌 경우(403 not_project_admin).
    """
    await authz.require_project_admin(db, project_id, user)
    return await maiu_service.retry_failed(
        db, project_id, list(payload.video_ids or []), user.user_id,
    )


@router.post(
    "/projects/{project_id}/maiu/dispatch",
    response_model=MaiuDispatchResult,
    summary="MI 요청 — available work_items를 외부에 전송 (project_admin)",
    tags=["maiu"],
)
async def dispatch(
    project_id: UUID,
    payload: MaiuDispatchRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """available 상태의 work_item을 MI(외부 분석)에 분석 요청한다. project_admin만 부를 수 있다.

    Raises:
        HTTPException: 분석 대상을 기록하지 않았거나 점검이 실패한 경우(409, `project_stack.gate`).
    """
    await project_stack.gate(db, project_id, user, analysis=True)
    return await maiu_service.dispatch(db, project_id, payload, user)


@router.get(
    "/projects/{project_id}/maiu/jobs",
    response_model=list[MaiuJobDTO],
    summary="MI 요청 이력",
    tags=["maiu"],
)
async def list_jobs(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """프로젝트의 MI 요청 job 이력 목록을 돌려준다."""
    return await maiu_service.list_for_project(db, project_id, user)


@router.get(
    "/projects/{project_id}/maiu/cost-stats",
    summary="MAIU 비용 통계 (project_admin)",
    tags=["maiu"],
)
async def maiu_cost_stats(
    project_id: UUID,
    month: str | None = Query(None, description="YYYY-MM"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """단계별 MAIU 호출 건수와 추정 비용(KRW)을 돌려준다."""
    return await maiu_service.cost_stats(db, project_id, user, month=month)
