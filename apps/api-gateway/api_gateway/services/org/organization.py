"""기본 조직을 찾는다.

조직 관리 화면이 없고 조직은 한 행뿐이다. 프로젝트와 자격증명 연결은 그 행에 속한다.
행이 하나라는 것은 마이그레이션(0101)의 유일 인덱스가 보장하므로 서버는 그 행을 찾아 쓰기만 한다.
RebornStudio Pro와 같은 스키마를 유지하려고 `org_id` 컬럼을 남긴다.
"""
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Organization


async def default_org_id(db: AsyncSession) -> UUID:
    """기본 조직의 id를 돌려준다.

    Raises:
        HTTPException: 조직 행이 없는 경우(500 `default_org_missing`). 마이그레이션이 만들므로
            정상 설치에서는 나지 않는다.
    """
    org_id = await db.scalar(select(Organization.org_id).limit(1))
    if org_id is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "default_org_missing")
    return org_id
