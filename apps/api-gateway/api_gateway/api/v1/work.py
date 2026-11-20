"""라벨러의 다음 작업·제출과 라벨·검수 이력 조회를 다루는 라벨링 작업 API 라우터."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from reborn_contracts import (
    DeidentMissReport,
    DeidentReportPage,
    DeidentReportResolveRequest,
    DeidentReportResolveResult,
    LabelDTO,
    MyWorkItemDTO,
    NextWorkResponse,
    RepeatedRejectPage,
    ReviewDTO,
    WorkDraftRequest,
    WorkItemDTO,
    WorkQcDTO,
    WorkSubmitRequest,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import User
from api_gateway.services.work import deident_reports, repeated_rejects
from api_gateway.services.work import review as review_service
from api_gateway.services.work import service as work_service

router = APIRouter()


@router.get(
    "/projects/{project_id}/work/available-count",
    response_model=dict[str, int],
    summary="지금 배정할 수 있는 라벨링 작업 수 + 내 보유 수 (멤버)",
    tags=["work"],
)
async def work_available_count(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, int]:
    """지금 배정할 수 있는 라벨링 작업 수(`count`)와 호출한 사용자의 보유 수(`held`)를 돌려준다.

    프로젝트 멤버가 부를 수 있다. 응답에는 수만 싣는다.
    다음 작업은 서버가 순서를 정해 한 건을 배정하므로 사용자가 고를 수 없는 목록은 내보내지 않는다.
    수는 `next_work`와 같은 조건(`_claimable_where`)으로 센다.
    """
    return await work_service.claimable_summary(db, project_id, user)


@router.get(
    "/projects/{project_id}/work/repeated-rejects",
    response_model=RepeatedRejectPage,
    summary="반려가 여러 번 쌓인 미완 작업 목록 (관리자)",
    tags=["work"],
)
async def list_repeated_rejects(
    project_id: UUID,
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RepeatedRejectPage:
    """관리자가 가이드나 클립을 살펴볼 반려 누적 작업을 돌려준다. 작업 흐름은 바꾸지 않는다."""
    return await repeated_rejects.list_for_project(db, project_id, user, limit=limit)


@router.get(
    "/projects/{project_id}/deident-reports",
    response_model=DeidentReportPage,
    summary="비식별화 미흡 신고로 작업이 멈춘 클립 목록 (관리자)",
    tags=["work"],
)
async def list_deident_reports(
    project_id: UUID,
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DeidentReportPage:
    """관리자가 재개·제외를 정할 신고 클립을 가장 최근 신고와 함께 돌려준다."""
    return await deident_reports.list_for_project(db, project_id, user, limit=limit)


@router.post(
    "/clips/{clip_id}/deident-report/resolve",
    response_model=DeidentReportResolveResult,
    summary="비식별화 신고 처리 — 작업 재개 또는 클립 제외 (프로젝트 관리자)",
    tags=["work"],
)
async def resolve_deident_report(
    clip_id: UUID,
    payload: DeidentReportResolveRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DeidentReportResolveResult:
    """비식별화 신고를 처리한다.

    재개하면 멈춘 작업이 대기열로 돌아가고, 제외하면 클립이 납품에서 빠지고 미완 작업이 닫힌다.
    """
    return await deident_reports.resolve(db, clip_id, payload, user)


@router.post(
    "/projects/{project_id}/work/next",
    response_model=NextWorkResponse,
    summary="다음 라벨링 작업 한 건 (보유한 작업이 있으면 그 작업)",
    tags=["work"],
)
async def next_work(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """호출한 사용자의 다음 라벨링 작업을 돌려준다.

    보유한 작업이 있으면 그 작업을 다시 주고(`resumed`), 없으면 대기열에서 한 건을 배정한다.
    보유는 제출하거나 비식별화 미흡을 신고할 때까지 이어진다.
    """
    return await work_service.next_work(db, project_id, user)


@router.post(
    "/work/{work_item_id}/submit",
    response_model=WorkItemDTO,
    summary="라벨링 결과 제출",
    tags=["work"],
)
async def submit(
    work_item_id: UUID,
    payload: WorkSubmitRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """라벨링 결과를 저장하고 work_item을 submitted 상태로 바꾼다."""
    return await work_service.submit(db, work_item_id, payload, user)


@router.patch(
    "/work/{work_item_id}/draft",
    response_model=WorkItemDTO,
    summary="임시저장 (30s 자동/Ctrl+S)",
    tags=["work"],
)
async def save_draft(
    work_item_id: UUID,
    payload: WorkDraftRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """작성 중인 라벨을 draft 컬럼에 임시 저장한다. 제출 payload와 따로 저장한다.

    작업을 점유한 사람만 부를 수 있다.
    """
    return await work_service.save_draft(db, work_item_id, payload.payload, user)


@router.post(
    "/work/{work_item_id}/report-deident-miss",
    status_code=204,
    summary="비식별화 미흡 보고",
    tags=["work"],
)
async def report_deident_miss(
    work_item_id: UUID,
    payload: DeidentMissReport,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """라벨러나 검수자가 비식별화 누락을 신고하면 클립을 legal_review로 바꾸고 작업을 멈춘다."""
    await work_service.report_deident_miss(
        db, work_item_id, payload.timestamp_ms, payload.reason, user
    )


@router.get(
    "/work/me",
    response_model=list[MyWorkItemDTO],
    summary="내가 들고있는 작업 목록",
    tags=["work"],
)
async def my_claims(
    project_id: UUID | None = Query(default=None, description="이 프로젝트 것만"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """현재 사용자가 점유 중인 work_item 목록을 다음 작업이 여는 순서로 돌려준다.

    `project_id`를 주면 그 프로젝트의 작업만 돌려준다.
    재작업이면 마지막 반려 사유(`last_reject_reasons`)를 함께 싣는다.
    """
    return await work_service.list_mine(db, user, project_id=project_id)


@router.get(
    "/work/{work_item_id}",
    response_model=WorkItemDTO,
    summary="작업 단건 조회 (claimant 또는 reviewer)",
    tags=["work"],
)
async def get_one(
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """단일 work_item 상세 정보를 반환한다(claimant 또는 reviewer 전용)."""
    return await work_service.get_one(db, work_item_id, user)


@router.get(
    "/work/{work_item_id}/qc",
    response_model=WorkQcDTO,
    summary="캡션 품질검수 상태와 최신 결과 — 판정 근거 (claimant 또는 reviewer)",
    tags=["work"],
)
async def get_qc(
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """캡션 품질검수의 상태와 최신 결과를 돌려준다. 검수자 화면이 판정 근거로 표시한다.

    제출·승인은 검사를 기다리지 않고 커밋한다. 검사는 커밋 뒤에 워커가 한다.
    현재 버전의 검사가 끝나지 않았으면 `state`가 `running`이고 결과는 없다.
    화면은 검사가 끝날 때까지 다시 조회한다.
    `none`은 프로젝트가 품질검수를 켜지 않았거나 검사 기록이 없는 작업이라는 뜻이다.
    통과와 구분한다.
    권한은 단건 조회와 같다(claimant 또는 reviewer).
    """
    await work_service.get_one(db, work_item_id, user)   # 권한 검사를 재사용한다
    return await work_service.latest_qc(db, work_item_id)


@router.get(
    "/work/{work_item_id}/labels",
    response_model=list[LabelDTO],
    summary="revision chain (claimant 또는 reviewer)",
    tags=["work"],
)
async def list_labels(
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """work_item의 라벨 revision chain 전체를 반환한다(claimant 또는 reviewer 전용)."""
    return await work_service.list_labels(db, work_item_id, user)


@router.get(
    "/work/{work_item_id}/reviews",
    response_model=list[ReviewDTO],
    summary="검수 이력 (claimant 또는 reviewer)",
    tags=["work"],
)
async def list_reviews(
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """work_item의 검수 판정 이력 전체를 반환한다(claimant 또는 reviewer 전용)."""
    return await review_service.list_reviews(db, work_item_id, user)
