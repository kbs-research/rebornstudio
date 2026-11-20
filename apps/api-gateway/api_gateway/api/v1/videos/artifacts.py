"""영상 한 편의 분석본 정리 API. 원본이 있을 때만 지운다."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as st
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import User, Video
from api_gateway.services import audit, authz

router = APIRouter()


@router.delete(
    "/videos/{video_id}/artifacts/analysis",
    summary="분석본 정리 — 원본이 있을 때만 (project_admin)",
    tags=["videos"],
)
async def purge_video_analysis(
    video_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """분석본 사본을 버킷에서 지운다. project_admin만 부를 수 있다.

    파생물은 캐시라는 규칙을 따른다.
    원본이 있으면 다시 인코딩해 언제든 만들 수 있으므로 지울 수 있다.
    원본이 없으면 이 사본이 유일한 파일이라 지우면 되돌릴 수 없다.
    그래서 409와 함께 지울 수 없는 사유를 문장으로 돌려준다.
    판정은 `retention.analysis_blocker` 한 곳에서 한다.

    Raises:
        HTTPException: 영상이 없는 경우(404 video_not_found).
            project_admin이 아닌 경우(403 not_project_admin).
            아직 쓰이거나 유일본이거나 사본이 없는 경우(409).
    """
    from api_gateway.services.media import retention

    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(st.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_project_admin(db, video.project_id, user)

    freed = await retention.purge_analysis(db, video)
    audit.emit(
        db, event_type="media.analysis_purged", target_kind="video",
        target_id=video_id, actor_user_id=user.user_id, project_id=video.project_id,
        payload={"freed_bytes": freed},
    )
    await db.commit()
    return {"freed_bytes": freed}
