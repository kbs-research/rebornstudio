"""반입 묶음(등록 Job)의 목록, 상세, 이름·메모 수정, 영상 메타 일괄 입력을 다루는 API."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from reborn_contracts import (
    BulkMetaApplyRequest,
    BulkMetaApplyResult,
    BulkMetaPreview,
    IngestBatchDTO,
    IngestBatchUpdate,
    IngestJobDetailDTO,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import User
from api_gateway.services.media import ingest_batch as batch_service
from api_gateway.services.media import meta_import
from api_gateway.services.media.meta_sheet import MAX_FILE_BYTES

router = APIRouter()


@router.get(
    "/projects/{project_id}/ingest-batches",
    response_model=list[IngestBatchDTO],
    summary="반입 묶음 목록 (멤버)",
    tags=["videos"],
)
async def list_ingest_batches(
    project_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = Query(default=None, description="cursor: 마지막 항목 created_at"),
    before_id: UUID | None = Query(default=None, description="cursor: 마지막 항목 batch_id"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """반입 묶음을 최신순으로 돌려준다. 프로젝트 멤버가 부를 수 있다.

    각 묶음의 반입 상태 분포, 총 길이, 판정 대기 클립 수를 포함한다.
    이 목록이 있어서 반입 직후의 탭을 닫아도 묶음 화면을 다시 찾을 수 있다.
    """
    return await batch_service.list_for_project(
        db, project_id, user, limit=limit, before=before, before_id=before_id,
    )


@router.get(
    "/ingest-batches/{batch_id}",
    response_model=IngestJobDetailDTO,
    summary="등록 Job 상세 (관리자)",
    tags=["videos"],
)
async def get_ingest_job(
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """등록 Job 한 건의 상세를 돌려준다. 관리자가 부를 수 있다.

    수행자, 이슈 분해, 클립 산출, 준비 소요 시간까지 담는다.
    목록(`/projects/{id}/ingest-batches`)은 행마다 join을 붙이면 비용이 커서 간단하게 둔다.
    상세는 1건이라 화면에 필요한 정보를 여기서 모두 채운다.
    """
    return await batch_service.get_detail(db, batch_id, user)


@router.patch(
    "/ingest-batches/{batch_id}",
    response_model=IngestBatchDTO,
    summary="반입 묶음 이름·메모 수정 (관리자)",
    tags=["videos"],
)
async def update_ingest_batch(
    batch_id: UUID,
    payload: IngestBatchUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """반입 묶음에 이름과 메모를 붙인다. 관리자가 부를 수 있다.

    나중에 어떤 반입이었는지 알아볼 수 있게 한다.
    """
    return await batch_service.update(db, batch_id, payload, user)


@router.get(
    "/ingest-batches/{batch_id}/meta-template",
    response_class=Response,
    responses={200: {"content": {meta_import.XLSX_MIME: {}}, "description": "양식 파일(.xlsx)"}},
    summary="영상 메타 일괄 입력 양식 내려받기 (관리자)",
    tags=["videos"],
)
async def download_meta_template(
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """묶음의 모든 영상과 지금 메타를 채운 양식(.xlsx)을 내려준다. 관리자가 부를 수 있다.

    열은 영상 ID·파일·제목·방송사·프로그램명·장르·회차·방영일이다.
    영상 ID와 파일은 영상을 찾는 열이고, 둘째 시트에 입력 규칙을 적는다.
    """
    content, filename = await meta_import.template(db, batch_id, user)
    return Response(
        content=content, media_type=meta_import.XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/ingest-batches/{batch_id}/meta-import/preview",
    response_model=BulkMetaPreview,
    summary="영상 메타 일괄 입력 미리 보기 (관리자)",
    tags=["videos"],
)
async def preview_meta_import(
    batch_id: UUID,
    file: UploadFile = File(description="채운 양식(.xlsx) 또는 UTF-8·CP949 CSV. 1 MiB까지"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """올린 파일의 행을 묶음 영상과 맞추고 바뀌는 값과 오류를 돌려준다. 저장하지 않는다.

    영상 ID, 파일, 제목 순서로 영상을 찾는다. 빈 셀은 바꾸지 않는다.
    파일이 1 MiB를 넘으면 413 `meta_import_file_too_large`, 읽지 못하면 422 `meta_import_*`다.
    """
    # 한도보다 1바이트 더 읽는다. 넘는 파일을 끝까지 메모리에 올리지 않고 한도 초과를 판정한다.
    data = await file.read(MAX_FILE_BYTES + 1)
    return await meta_import.preview(db, batch_id, user, data, file.filename)


@router.post(
    "/ingest-batches/{batch_id}/meta-import/apply",
    response_model=BulkMetaApplyResult,
    summary="영상 메타 일괄 입력 반영 (관리자)",
    tags=["videos"],
)
async def apply_meta_import(
    batch_id: UUID,
    payload: BulkMetaApplyRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """미리 보기에서 확인한 변경을 한 트랜잭션으로 저장한다. 관리자가 부를 수 있다.

    값은 영상 메타 편집과 같은 규칙으로 다시 검사한다. 하나라도 실패하면 아무것도 바꾸지 않는다.
    이 묶음에 없는 영상이 있으면 409 `meta_import_video_not_in_batch`다.
    """
    return await meta_import.apply(db, batch_id, payload, user)
