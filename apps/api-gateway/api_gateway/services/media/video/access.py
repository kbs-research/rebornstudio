"""영상 서비스 함수가 공통으로 쓰는 권한 확인."""
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import User, Video
from api_gateway.services import authz


async def _load_for_admin(db: AsyncSession, video_id: UUID, user: User) -> Video:
    """영상을 읽고 project_admin 권한을 확인한 뒤 Video를 돌려준다."""
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_project_admin(db, video.project_id, user)
    return video


async def _require_member(db: AsyncSession, project_id: UUID, user: User) -> None:
    """프로젝트 멤버(또는 super_admin)가 아니면 403을 낸다. 판정은 `authz`에 맡긴다."""
    await authz.require_member(db, project_id, user)
