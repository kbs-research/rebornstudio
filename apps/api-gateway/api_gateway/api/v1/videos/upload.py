"""업로드 상한, 멀티파트 시작·파트 서명·완료 확정을 다루는 영상 파일 업로드 API."""
from uuid import UUID

from fastapi import APIRouter, Depends
from reborn_contracts import (
    VideoDTO,
    VideoMultipartInitiateResponse,
    VideoPartUrlsRequest,
    VideoPartUrlsResponse,
    VideoUploadCompleteRequest,
    VideoUploadLimits,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import User, Video
from api_gateway.services.media import video as video_service
from api_gateway.services.stacks import project_stack

router = APIRouter()


@router.get(
    "/projects/{project_id}/videos/upload-limits",
    response_model=VideoUploadLimits,
    summary="로컬 업로드 상한·확장자 — 받기 전에 거르는 재료",
    tags=["videos"],
)
async def video_upload_limits(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """로컬 업로드의 크기 상한과 허용 확장자를 돌려준다. 프로젝트 멤버가 부를 수 있다.

    등록 시트가 열릴 때 한 번 조회한다. 화면이 상한을 미리 알아야 큰 파일을 영상 레코드를 만들기
    전에 거른다. 레코드를 만든 뒤에 걸리면 파일 없는 영상 레코드가 남는다.
    """
    return await video_service.upload_limits(db, project_id, user)


async def _gate(db: AsyncSession, video_id: UUID, user: User) -> None:
    """업로드를 시작하기 전에 영상의 프로젝트가 분석 대상을 기록했고 점검을 통과하는지 확인한다.

    영상이 없으면 아무것도 하지 않는다. 404는 업로드 서비스가 낸다.
    """
    video = await db.get(Video, video_id)
    if video is not None:
        await project_stack.gate(db, video.project_id, user)


@router.post(
    "/videos/{video_id}/upload/multipart",
    response_model=VideoMultipartInitiateResponse,
    summary="멀티파트 업로드 시작·이어받기 (project_admin)",
    tags=["videos"],
)
async def initiate_multipart(
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """멀티파트 업로드를 시작하거나, 진행 중인 업로드의 upload_id와 올라간 파트를 돌려준다.

    project_admin만 부를 수 있다. 시작 전에 분석 대상을 기록했고 점검을 통과하는지 확인한다.

    Raises:
        HTTPException: 영상이 없는 경우(404 video_not_found).
            project_admin이 아닌 경우(403 not_project_admin).
            이미 업로드된 경우(409 already_uploaded).
            분석 대상을 기록하지 않았거나 점검이 실패한 경우(409, `project_stack.gate`).
    """
    await _gate(db, video_id, user)
    return await video_service.initiate_multipart(db, video_id, user)


@router.post(
    "/videos/{video_id}/upload/parts",
    response_model=VideoPartUrlsResponse,
    summary="파트 presigned PUT URL 발급 (project_admin)",
    tags=["videos"],
)
async def sign_upload_parts(
    video_id: UUID,
    payload: VideoPartUrlsRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """요청한 파트 번호의 presigned PUT URL만 발급한다. project_admin만 부를 수 있다.

    한 번에 모두 받으면 느린 회선에서 뒤쪽 URL이 쓰기 전에 만료된다.
    """
    return await video_service.sign_upload_parts(db, video_id, payload, user)


@router.post(
    "/videos/{video_id}/upload/complete",
    response_model=VideoDTO,
    summary="업로드 완료 확정 (HEAD 검증 후 source_uri 기록)",
    tags=["videos"],
)
async def complete_upload(
    video_id: UUID,
    payload: VideoUploadCompleteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """S3 HEAD 검증 후 업로드 완료를 확정하고 source_uri를 영상 레코드에 기록한다."""
    return await video_service.complete_upload(db, video_id, payload, user)
