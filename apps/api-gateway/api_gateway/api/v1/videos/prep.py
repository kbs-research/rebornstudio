"""준비 단계의 타임라인과 단계별 재시도를 다루는 API."""
from uuid import UUID

from fastapi import APIRouter, Depends, status
from reborn_contracts import (
    PrepTimelineDTO,
    VideoDTO,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import User
from api_gateway.services.media import deident as deident_service
from api_gateway.services.media import scene_split as scene_split_service
from api_gateway.services.media import transcode as transcode_service
from api_gateway.services.media import video as video_service
from api_gateway.services.media import video_thumbnail

router = APIRouter()


@router.get(
    "/videos/{video_id}/prep-timeline",
    response_model=PrepTimelineDTO,
    summary="준비 타임라인",
    tags=["videos"],
)
async def get_prep_timeline(
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """영상의 준비 단계별 현재 상태와 실패·건너뜀 사유, 재시도·건너뛰기 가능 여부를 돌려준다.

    라벨러를 뺀 프로젝트 역할이 부를 수 있다.
    단계 이력이 없는 단계는 현재 상태 열에서 계산한다.
    """
    return await video_service.prep_timeline(db, video_id, user)


@router.post(
    "/projects/{project_id}/videos/{video_id}/deident/retry",
    status_code=204,
    summary="비식별화 재시도 — 실패한 것만 (project_admin)",
    tags=["videos"],
)
async def retry_deident(
    project_id: UUID,
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """실패한 비식별화를 다시 큐에 넣는다. project_admin만 부를 수 있다.

    완료된 비식별화는 409로 거절한다.
    마스킹본이 마스터를 대체했으므로 다시 실행하면 이미 가린 부분을 또 가린다.
    실행 중이거나 자동 재시도를 기다리는 작업도 `deident_in_progress`로 거절한다.
    규격 검증 전이거나 변환 중이라 비식별화 단계가 아직 열리지 않은 영상은
    `deident_waiting`으로 거절한다.
    실행 중에 멈춘 작업은 리스가 만료되면 회수되어 다시 실행된다.
    일시 오류는 간격을 두고 다시 보낸다.
    """
    await deident_service.retry(db, project_id, video_id, user)


@router.post(
    "/projects/{project_id}/videos/{video_id}/deident/skip",
    status_code=204,
    summary="비식별화 건너뛰기 — 끈 프로젝트의 실패한 것만 (project_admin)",
    tags=["videos"],
)
async def skip_deident(
    project_id: UUID,
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """비식별화를 끈 프로젝트에서 실패로 확정된 영상을 건너뜀으로 넘기고 재생본 단계를 연다.

    project_admin만 부를 수 있다. 비식별화하지 않은 원본으로 다음 단계가 진행된다.
    비식별화를 켠 프로젝트는 409 `deident_enabled`로 거절한다.
    켠 프로젝트에서 넘기면 가리지 않은 원본이 분석으로 나가기 때문이다.
    실패로 확정된 영상이 아니면 409 `deident_not_failed`를 돌려준다.
    누가 넘겼는지와 직전 실패 사유는 감사 기록(`video.deident_skip_manual`)에 남는다.
    """
    await deident_service.skip_failed(db, project_id, video_id, user)


@router.post(
    "/projects/{project_id}/videos/{video_id}/scene-split/retry",
    status_code=204,
    summary="장면 분할 다시 실행 — 실패한 것만 (project_admin)",
    tags=["videos"],
)
async def retry_scene_split(
    project_id: UUID,
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """실패한 장면 분할을 다시 연다. project_admin만 부를 수 있다.

    다음 투입 주기(1분)에 장면 분할이 다시 실행된다.
    클립이 이미 있으면 409 `scene_split_has_clips`로 거절한다. 다시 자르면 클립이 중복으로 생긴다.
    실패 상태가 아니거나 자를 준비가 끝나지 않았으면 409로 거절한다.
    """
    await scene_split_service.retry(db, project_id, video_id, user)


@router.post(
    "/projects/{project_id}/videos/{video_id}/transcode/retry",
    status_code=204,
    summary="변환 재시도 — 분석본·재생본 중 상한에 걸려 실패한 단계 (project_admin)",
    tags=["videos"],
)
async def retry_transcode(
    project_id: UUID,
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """`failed`로 멈춘 변환을 pending으로 되돌린다. project_admin만 부를 수 있다.

    다음 투입 주기(30초)에 변환이 다시 실행된다.
    분석본 변환이 실패했으면 분석본 변환을, 아니면 실패한 재생본 변환(publish)을 되돌린다.
    화면은 둘을 한 이름(`변환 재시도`)으로 부른다.
    산출 검사에 떨어져 멈춘 변환도 이 경로로 다시 실행한다.
    실행 중이거나 끝난 변환은 409 `transcode_not_failed`로 거절한다.

    규격 재검사(`probe/reset`)와는 다른 경로다.
    변환만 실패한 영상은 규격 검사를 통과한 상태라 `probe/reset`이 409를 낸다.
    """
    await transcode_service.retry(db, project_id, video_id, user)


@router.post(
    "/projects/{project_id}/videos/{video_id}/thumbnails/retry",
    status_code=204,
    summary="썸네일 다시 만들기 — 상한에 걸려 멈춘 것만 (project_admin)",
    tags=["videos"],
)
async def retry_thumbnails(
    project_id: UUID,
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """대표 이미지와 클립 썸네일의 실패 표시를 지워 다음 주기(1분)에 다시 만들게 한다.

    project_admin만 부를 수 있다.
    멈춘 썸네일이 없으면 409 `thumbnail_not_failed`를 돌려준다.
    재시도 중인 썸네일은 워커가 자동으로 다시 만든다.
    """
    await video_thumbnail.retry_failed(db, project_id, video_id, user)


@router.post(
    "/projects/{project_id}/videos/{video_id}/probe/reset",
    response_model=VideoDTO,
    status_code=status.HTTP_200_OK,
    summary="영상 규격 검사(probe) 재시도 — error/rejected→pending (project_admin)",
    tags=["videos"],
)
async def reset_video_probe(
    project_id: UUID,
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """멈춘 규격 검사 결과를 pending으로 되돌려 다음 배치가 다시 검사하게 한다.

    presign 오라우팅 같은 인프라 문제로 error가 된 영상을 운영자가 다시 검사할 때 쓴다.

    Raises:
        HTTPException: project_admin이 아닌 경우(403). 영상이 없는 경우(404).
            error/rejected 상태가 아닌 경우(409 not_resettable).
    """
    return await video_service.reset_probe(db, project_id, video_id, user)
