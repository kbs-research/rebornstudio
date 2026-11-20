"""영상 목록·집계·상세와 등록·수정·삭제, 재생 주소 발급을 다루는 API."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from reborn_contracts import (
    IngestBucket,
    PrepStage,
    VideoBucketCounts,
    VideoCreateRequest,
    VideoDTO,
    VideoSelectRequest,
    VideoSelectResult,
    VideoSourceURLResponse,
    VideoUpdateRequest,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import Project, User
from api_gateway.services.analysis import presets
from api_gateway.services.media import video as video_service
from api_gateway.services.stacks import project_stack

router = APIRouter()


@router.post(
    "/projects/{project_id}/videos",
    response_model=VideoDTO,
    status_code=status.HTTP_201_CREATED,
    summary="영상 등록 (project_admin)",
    tags=["videos"],
)
async def create_video(
    project_id: UUID,
    payload: VideoCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """프로젝트에 새 영상 메타데이터를 등록한다(project_admin 전용).

    저장소 대상(MI 방식이면 분석 대상도)을 기록하지 않았거나 점검이 실패하면 409를 돌려준다
    (`project_stack.gate`).
    고른 저장소 연결을 읽지 못하면 영상 행을 만들기 전에 409 `storage_not_configured`를 낸다.
    행을 만든 뒤 업로드에서 막히면 파일 없는 영상이 남는다.
    """
    project = await db.get(Project, project_id)
    if project is not None:
        await project_stack.gate(db, project_id, user)
        presets.require(presets.of_project(project.settings), project_settings=project.settings)
    return await video_service.create(db, project_id, payload, user)


@router.get(
    "/projects/{project_id}/videos",
    response_model=list[VideoDTO],
    summary="프로젝트 영상 목록",
    tags=["videos"],
)
async def list_videos(
    project_id: UUID,
    bucket: IngestBucket | None = Query(default=None, description="반입 상태 필터"),
    prep: PrepStage | None = Query(default=None, description="준비 여정 필터"),
    batch: UUID | None = Query(default=None, description="반입 배치 — 이 묶음만"),
    q: str | None = Query(default=None, max_length=200,
                          description="검색 — 제목·원본 경로·방송사·프로그램 부분일치"),
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = Query(default=None, description="cursor: 마지막 항목 created_at"),
    before_id: UUID | None = Query(default=None, description="cursor: 마지막 항목 video_id"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """프로젝트 영상 목록을 최신순 커서 페이지로 돌려준다. 프로젝트 멤버가 부를 수 있다.

    `q`는 서버에서 거른다. 화면이 커서 페이지에 실린 행만 찾으면 다른 페이지의 영상을
    "없음"으로 잘못 표시한다.
    """
    return await video_service.list_for_project(
        db, project_id, user,
        bucket=bucket, prep=prep, batch=batch, q_text=q,
        limit=limit, before_at=before, before_id=before_id,
    )


@router.get(
    "/projects/{project_id}/videos/buckets",
    response_model=VideoBucketCounts,
    summary="반입 상태별 건수",
    tags=["videos"],
)
async def video_bucket_counts(
    project_id: UUID,
    batch: UUID | None = Query(default=None, description="반입 배치 — 이 묶음만"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """반입 상태별 건수, 복구 가능 수, 판정 대기 클립 수, 이슈 분해를 돌려준다.

    프로젝트 멤버가 부를 수 있다.
    """
    return await video_service.bucket_counts(db, project_id, user, batch=batch)


@router.post(
    "/projects/{project_id}/videos/select",
    response_model=VideoSelectResult,
    summary="조건 기준 선택 — 조건을 id 목록으로",
    tags=["videos"],
)
async def select_videos(
    project_id: UUID,
    payload: VideoSelectRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """조건(Job·단계·상태·검색)에 맞는 영상 id를 모두 돌려준다. 프로젝트 멤버가 부를 수 있다.

    화면이 "대기 88건 전부"를 처리하려면 그 id가 모두 필요하다. 커서 목록에는 불러온 행만 있으므로
    전체 분모를 아는 서버가 조건을 해석한다. 상한에 걸리면 `truncated`로 알린다.
    """
    return await video_service.select_ids(db, project_id, user, payload)


@router.get(
    "/videos/{video_id}",
    response_model=VideoDTO,
    summary="영상 상세",
    tags=["videos"],
)
async def get_video(
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """단일 영상 메타데이터를 반환한다(멤버 전용)."""
    return await video_service.get_one(db, video_id, user)


@router.delete(
    "/videos/{video_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="영상 soft delete (project_admin)",
    tags=["videos"],
)
async def delete_video(
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """영상을 soft delete한다(deleted_at 기록). project_admin만 부를 수 있다.

    행을 지우는 hard delete는 하지 않는다.
    """
    await video_service.soft_delete(db, video_id, user)


@router.get(
    "/videos/{video_id}/source-url",
    response_model=VideoSourceURLResponse,
    summary="브라우저 player용 presigned GET URL (멤버 권한)",
    tags=["videos"],
)
async def get_source_url(
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """브라우저 영상 재생을 위한 presigned GET URL을 발급한다(멤버 전용)."""
    return await video_service.issue_source_url(db, video_id, user)


@router.patch(
    "/videos/{video_id}",
    response_model=VideoDTO,
    summary="영상 메타 편집 (관리자)",
    tags=["videos"],
)
async def update_video(
    video_id: UUID,
    payload: VideoUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """제목·회차와 영상 메타 네 가지(방송사·프로그램명·장르·방영일)를 수정한다.

    관리자가 부를 수 있다. 메타 네 가지는 클립이 물려받는다.
    보내지 않은 필드는 그대로 두고, `null`을 보낸 필드는 지운다. 제목은 지울 수 없다.
    """
    return await video_service.update(db, video_id, payload, user)
