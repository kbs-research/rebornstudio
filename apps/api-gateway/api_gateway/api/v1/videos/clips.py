"""클립 직접 추가, 프로젝트·영상별 클립 조회를 다루는 API."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from reborn_contracts import (
    ClipCountDTO,
    ClipCreateRequest,
    ClipDTO,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import User
from api_gateway.services.media import clip as clip_service

router = APIRouter()


@router.post(
    "/projects/{project_id}/videos/{video_id}/clips",
    response_model=ClipDTO,
    status_code=201,
    summary="씬 구간 직접 추가 (project_admin)",
    tags=["clips"],
)
async def create_clip_manually(
    project_id: UUID,
    video_id: UUID,
    payload: ClipCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """사람이 씬 구간 하나를 만든다. project_admin만 부를 수 있다.

    MI 스택에서는 MI가 항상 씬을 만들므로 보정 기능으로 쓰인다.
    분석기가 구간을 만들지 않는 스택에서는 씬을 만드는 유일한 방법이다.
    그래서 빈 목록의 첫 액션이 "직접 추가"다.
    같은 구간이 이미 있으면 409를 돌려준다.
    """
    return await clip_service.create_manual(db, project_id, video_id, payload, user)


@router.get(
    "/projects/{project_id}/clips",
    response_model=list[ClipDTO],
    summary="프로젝트 전체 clips (콘텐츠 클립 탭 + 구 ClipSearch, 멤버)",
    tags=["videos"],
)
async def list_clips_for_project(
    project_id: UUID,
    q: str | None = Query(default=None, max_length=200,
                          description="검색 — 영상 제목·장면 요약 부분일치"),
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = Query(default=None, description="cursor: 마지막 항목 created_at"),
    before_video_id: UUID | None = Query(default=None, description="cursor: 마지막 항목 video_id"),
    before_start: float | None = Query(
        default=None, description="cursor: 마지막 항목 start_seconds",
    ),
    before_id: UUID | None = Query(default=None, description="cursor: 마지막 항목 clip_id"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """프로젝트의 클립을 만든 순서의 최신순으로 돌려준다. 프로젝트 멤버가 부를 수 있다.

    같은 때 만든 클립은 영상별로 묶는다. 한 영상 안에서는 시작 시각 순이다.
    `q`는 서버에서 거른다. 화면이 불러온 행만 찾으면 다른 페이지의 클립을 "없음"으로
    잘못 표시한다.
    다음 페이지는 직전 페이지 마지막 항목의 (created_at, video_id, start_seconds, clip_id)
    커서로 받는다.

    Raises:
        HTTPException: 커서 넷 중 일부만 온 경우(422 incomplete_cursor).
    """
    return await clip_service.list_for_project(
        db, project_id, user, q_text=q, limit=limit,
        before_at=before, before_video_id=before_video_id,
        before_start=before_start, before_id=before_id,
    )


@router.get(
    "/projects/{project_id}/clips/count",
    response_model=ClipCountDTO,
    summary="프로젝트 clips 수 (콘텐츠 클립 탭 분모, 멤버)",
    tags=["videos"],
)
async def count_clips_for_project(
    project_id: UUID,
    q: str | None = Query(default=None, max_length=200,
                          description="검색 — 영상 제목·장면 요약 부분일치"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """목록(`GET /projects/{id}/clips`)과 같은 조건으로 클립 수를 센다. 멤버가 부를 수 있다.

    목록은 커서로 나눠 받으므로 화면은 받은 행 수만 알 수 있다. 이 수가 "N개 중"의 분모다.
    `/clips/{clip_id}`보다 먼저 선언한다. 뒤에 두면 `count`를 클립 id로 읽어 422가 난다.
    """
    return await clip_service.count_for_project(db, project_id, user, q_text=q)


@router.get(
    "/projects/{project_id}/clips/{clip_id}",
    response_model=ClipDTO,
    summary="단건 clip 조회 (멤버)",
    tags=["videos"],
)
async def get_clip(
    project_id: UUID,
    clip_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """클립 한 건을 돌려준다. 프로젝트 멤버가 부를 수 있다.

    라벨링 화면(work/label)이 전체 목록을 받는 대신 이 API를 쓴다.
    """
    return await clip_service.get_one(db, project_id, clip_id, user)


@router.get(
    "/projects/{project_id}/videos/{video_id}/clips",
    response_model=list[ClipDTO],
    summary="비디오의 MI 분석 결과 clips (멤버)",
    tags=["videos"],
)
async def list_clips_for_video(
    project_id: UUID,
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """영상의 모든 클립을 start_seconds 오름차순으로 돌려준다. 프로젝트 멤버가 부를 수 있다.

    MI 분석, 샷 검출, 사람이 만든 구간을 구분 없이 모두 돌려준다.
    """
    return await clip_service.list_for_video(db, project_id, video_id, user)
