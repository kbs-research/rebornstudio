"""내보내기 API 라우터.

납품 패키지 생성·다운로드, 완료 목록, 캡션 품질검수 보류를 다룬다.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from reborn_contracts import (
    CompletedDetail,
    CompletedFilter,
    CompletedPage,
    CompletedSummary,
    ExportDownloadResponse,
    ExportDTO,
    QcCaptionSentence,
    QcHeldPage,
    QcHeldRow,
    QcHoldActionResult,
    QcHoldReleaseRequest,
    QcHoldReviseRequest,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import User
from api_gateway.services import authz, caption
from api_gateway.services.caption_qc import hold as qc_hold
from api_gateway.services.caption_qc import qc_finding_dto, qc_stages_dto
from api_gateway.services.exports import export_service

router = APIRouter()


@router.post(
    "/projects/{project_id}/exports",
    response_model=ExportDTO,
    status_code=status.HTTP_201_CREATED,
    summary="내보내기 생성 — 승인분을 납품 패키지(zip)로 (project_admin)",
    tags=["exports"],
)
async def create_export(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """승인된 작업 전체를 납품 패키지(zip)로 묶는 내보내기를 만든다. project_admin만 부른다."""
    return await export_service.create(db, project_id, user)


@router.get(
    "/projects/{project_id}/exports",
    response_model=list[ExportDTO],
    summary="내보내기 이력",
    tags=["exports"],
)
async def list_exports(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """프로젝트의 내보내기 이력을 최신순으로 돌려준다. project_admin만 부를 수 있다.

    시도 수·제외 수·파일 이름을 포함한다.
    """
    return await export_service.list_for_project(db, project_id, user)


@router.get(
    "/exports/{export_id}/download",
    response_model=ExportDownloadResponse,
    summary="납품 패키지 다운로드 링크 (presigned)",
    tags=["exports"],
)
async def download_export(
    export_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """납품 패키지의 presigned GET URL을 발급한다. project_admin만 부를 수 있다.

    내려받을 파일 이름을 서명에 넣는다.
    """
    return await export_service.download(db, export_id, user)


@router.get(
    "/projects/{project_id}/completed",
    response_model=CompletedPage,
    summary="완료 목록 — 승인되어 내보내기에 실릴 작업 (라벨러 외 역할)",
    tags=["exports"],
)
async def list_completed(
    project_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, description="캡션·영상 제목·프로그램명 검색"),
    filter: CompletedFilter = Query(
        "all",
        description="거르기 축 — all(전체) · deliverable(납품 대상) · excluded(납품 제외) · "
                    "qc_rules(규칙만 검사). 판정은 서버 한 곳이다",
    ),
    summary: bool = Query(
        True,
        description="전수 집계(납품 대상·보류·규칙만 검사)를 같이 셀지. 화면은 false로 부르고 "
                    "`/completed/summary`에서 한 번 받는다",
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """완료 탭 목록을 돌려준다. 라벨러를 뺀 프로젝트 역할이 부를 수 있다.

    행 집합은 내보내기 매니페스트와 같은 쿼리로 만든다.
    `q`와 `filter`는 서버에서 전체를 대상으로 적용한다.
    페이지에 실린 행만 찾으면 다른 페이지에 있는 행을 "없음"으로 잘못 표시한다.
    `filter`의 판정은 기존 정의를 그대로 부른다.
    납품 여부는 패키지 검사(`export_package.check_row`)를 쓴다.
    규칙만 검사는 버튼 옆 경고를 세는 쿼리(`qc_rules_only_ids`)를 쓴다.
    """
    return await export_service.list_completed(
        db, project_id, user, limit=limit, offset=offset, q=q, summary=summary,
        completed_filter=filter,
    )


# 주의: `/completed/{work_item_id}`보다 먼저 등록한다.
# 뒤에 두면 `summary`가 id 경로 변수로 해석되어 UUID 검증에서 422가 난다.
@router.get(
    "/projects/{project_id}/completed/summary",
    response_model=CompletedSummary,
    summary="완료 목록 전수 집계 — 검색·페이지와 무관 (라벨러 외 역할)",
    tags=["exports"],
)
async def completed_summary(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """완료 전체·납품 대상·품질검수 보류·규칙만 검사한 건수를 한 번에 돌려준다.

    라벨러를 뺀 프로젝트 역할이 부를 수 있다.
    목록 페이지마다 세면 `더 보기`와 검색어 입력마다 최대 2,000행 검증이 다시 실행된다.
    """
    return await export_service.completed_summary(db, project_id, user)


@router.get(
    "/projects/{project_id}/qc/held",
    response_model=QcHeldPage,
    summary="캡션 품질검수 보류함 — 승인됐지만 납품이 막힌 작업 (관리자)",
    tags=["exports"],
)
async def list_qc_held(
    project_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """캡션 품질검수 보류함을 돌려준다. 관리자가 부를 수 있다.

    완료 목록 조건(`approved AND qc_status != held`)이 빼는 작업을 근거와 함께 돌려준다.
    이 목록이 있어야 운영자가 완료 수가 줄어든 이유를 알 수 있다.
    판정은 사람이 하고, 이 API는 근거만 돌려준다.
    """
    await authz.require_project_admin(db, project_id, user)
    rows, latest, recheck_failed, total = await export_service.list_qc_held(
        db, project_id, limit=limit, offset=offset,
    )
    items = []
    for w, v, c in rows:
        run = latest.get(w.work_item_id)
        # 표시용 캡션도 기준 캡션 값(`caption.final_text`)으로 읽는다.
        # `brief`만 읽으면 문장 배열만 담긴 작업이 보류 목록에서 빈 줄로 표시된다.
        # 그러면 보류를 푸는 사람이 근거를 볼 수 없다.
        items.append(QcHeldRow(
            work_item_id=w.work_item_id, video_id=w.video_id, clip_id=w.clip_id,
            start_seconds=c.start_seconds if c else None,
            end_seconds=c.end_seconds if c else None,
            video_title=v.title,
            caption_brief=caption.final_text(w.payload),
            verdict=run.verdict if run else None,
            findings=[qc_finding_dto(f) for f in (run.findings if run else [])],
            adapter_key=run.adapter_key if run else None,
            model=run.model if run else None,
            checked_at=run.created_at if run else None,
            recheck_failed_at=recheck_failed.get(w.work_item_id),
            stages=qc_stages_dto(run.stages) if run else None,
            # `고쳐 다시 승인` 창에서 고칠 문장.
            # 읽는 규칙은 검사·납품과 같은 `caption.sentences_of`다.
            sentences=[
                QcCaptionSentence(id=x["id"], text=x.get("text") or "")
                for x in caption.live_sentences(caption.sentences_of(w.payload))
                if isinstance(x.get("id"), str)
            ],
        ))
    return QcHeldPage(total=total, items=items)


@router.post(
    "/projects/{project_id}/qc/{work_item_id}/release",
    response_model=QcHoldActionResult,
    summary="캡션 품질검수 보류 해제 — 사유 필수 (관리자)",
    tags=["exports"],
)
async def release_qc_hold(
    project_id: UUID,
    work_item_id: UUID,
    payload: QcHoldReleaseRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """사람이 품질검수 보류를 해제한다. project_admin만 부를 수 있다.

    사유가 필수다. 해제 시점의 지적 목록 전체를 감사 기록에 남긴다.
    비속어 사전의 부분 일치 오탐처럼 같은 문장을 다시 승인해도 다시 걸리는 보류는
    이 경로로만 풀 수 있다.

    Raises:
        HTTPException: 작업이 없는 경우(404 work_item_not_found).
            project_admin이 아닌 경우(403 not_project_admin). 보류 중이 아닌 경우(409 qc_not_held).
    """
    return await qc_hold.release(db, project_id, work_item_id, payload.reason, user)


@router.post(
    "/projects/{project_id}/qc/{work_item_id}/recheck",
    response_model=QcHoldActionResult,
    summary="캡션 품질검수 다시 검사 — 승인 없이 검사만 (관리자)",
    tags=["exports"],
)
async def recheck_qc_hold(
    project_id: UUID,
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """사전·설정을 고친 뒤 품질검수만 다시 실행한다. project_admin만 부를 수 있다.

    통과하면 보류가 풀린다. 다시 걸리면 보류가 유지된다.
    이 API는 사람의 판정을 대신하지 않으므로 다시 걸린 보류를 풀지 않는다.
    사람이 풀려면 `보류 해제`를 쓴다.

    Raises:
        HTTPException: 작업이 없는 경우(404 work_item_not_found).
            project_admin이 아닌 경우(403 not_project_admin). 보류 중이 아닌 경우(409 qc_not_held).
    """
    return await qc_hold.recheck(db, project_id, work_item_id, user)


@router.post(
    "/projects/{project_id}/qc/{work_item_id}/revise",
    response_model=QcHoldActionResult,
    summary="캡션 품질검수 보류분의 캡션 고쳐 다시 승인 (관리자)",
    tags=["exports"],
)
async def revise_qc_hold(
    project_id: UUID,
    work_item_id: UUID,
    payload: QcHoldReviseRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """보류된 승인본의 캡션을 고쳐 다시 승인한다. project_admin만 부를 수 있다.

    고친 버전은 새 라벨 개정과 수정 후 승인 판정으로 남는다. 게이트는 `pending`으로 돌아간다.
    승인 검사는 워커가 다른 승인과 같은 경로로 다시 한다. 그 결과가 보류 여부를 정한다.

    Raises:
        HTTPException: 작업이 없는 경우(404 work_item_not_found).
            project_admin이 아닌 경우(403 not_project_admin). 보류 중이 아닌 경우(409 qc_not_held).
            없는 문장인 경우(422 sentence_not_found). 바뀐 문장이 없는 경우(422 caption_unchanged).
            제출 기준에 못 미치는 경우(422 caption_too_short).
    """
    return await qc_hold.revise(db, project_id, work_item_id, payload.sentences, user)


@router.get(
    "/projects/{project_id}/completed/{work_item_id}",
    response_model=CompletedDetail,
    summary="완료된 클립 상세 — 읽기 전용 (라벨러 외 역할)",
    tags=["exports"],
)
async def get_completed(
    project_id: UUID,
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """완료된 클립의 요약·라벨 내용·제출/검수 이력·납품 가능 여부를 한 번에 돌려준다.

    라벨러를 뺀 프로젝트 역할이 부를 수 있다.
    """
    return await export_service.get_completed(db, project_id, work_item_id, user)
