"""자격증명 연결 관리 API 라우터. 관리는 super_admin 전용이다.

연결은 기본 조직 한 곳에 속하므로 경로에 조직이 없다. 라우터가 기본 조직을 찾아 서비스에 넘긴다.

자격증명은 이 시스템에서 가장 민감한 값이라 진단과 같은 권한(super_admin)으로만 연다.
시크릿은 쓰기 전용이다. 넣을 수만 있고 응답에는 싣지 않는다.

예외는 하나다. 캡션 품질검수의 모델 연결은 프로젝트 관리자가 고른다. 그래서 프로젝트 관리자는
연결 이름 목록(`GET /projects/{id}/connection-choices`)을 조회할 수 있다.
이 응답에는 이름·프로바이더·시크릿 유무·마지막 점검 결과만 싣는다.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from reborn_contracts import (
    ConnectionChoiceDTO,
    ConnectionCreateRequest,
    ConnectionDTO,
    ConnectionSaved,
    ConnectionUpdateRequest,
)
from reborn_contracts.connection import ConnectionPurpose
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db, require_super_admin
from api_gateway.models import User
from api_gateway.services.org import organization as org_service
from api_gateway.services.stacks import connections as svc

router = APIRouter(prefix="/connections", tags=["connections"])
#: 프로젝트 설정이 쓰는 연결 선택지. 관리 라우터와 경로 접두어가 달라 따로 둔다.
choices_router = APIRouter(tags=["connections"])


@choices_router.get(
    "/projects/{project_id}/connection-choices", response_model=list[ConnectionChoiceDTO],
    summary="프로젝트 설정에서 고를 수 있는 연결 이름 (project_admin)",
)
async def connection_choices(
    project_id: UUID,
    purpose: ConnectionPurpose | None = Query(
        None, description="caption_qc(품질검수 모델 연결). 비우면 전부",
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ConnectionChoiceDTO]:
    """연결 이름 목록을 돌려준다. 설정값과 시크릿은 싣지 않는다.

    `purpose`를 주면 그 목적에 쓸 수 있는 연결만 돌려준다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404), 그 프로젝트의 관리자가 아닌 경우(403).
    """
    return await svc.choices_for_project(db, project_id, user, purpose)


@router.get("", response_model=list[ConnectionDTO], summary="연결 목록 (super_admin)")
async def list_connections(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
) -> list[ConnectionDTO]:
    """연결 목록을 돌려준다. 시크릿은 값 대신 설정 여부만 싣는다."""
    return await svc.list_for_org(db, await org_service.default_org_id(db))


@router.post("", response_model=ConnectionSaved, status_code=201,
             summary="연결 생성과 점검 (super_admin)")
async def create_connection(
    payload: ConnectionCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_super_admin),
) -> ConnectionSaved:
    """새 연결을 만들고 실제로 접속해 점검한다. 시크릿 없이 만들고 나중에 채울 수 있다.

    점검 결과는 응답의 `check`와 연결 행의 마지막 점검에 남는다. 점검이 실패해도 201이다.

    Raises:
        HTTPException: 같은 이름이 있는 경우(409 name_exists).
    """
    return await svc.create(db, await org_service.default_org_id(db), payload, user)


@router.patch("/{conn_id}", response_model=ConnectionSaved,
              summary="연결 수정과 점검 (super_admin)")
async def update_connection(
    conn_id: UUID,
    payload: ConnectionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_super_admin),
) -> ConnectionSaved:
    """연결을 수정하고 실제로 접속해 점검한다.

    `None`인 필드는 그대로 두고, 시크릿을 보내면 그 값으로 교체한다.

    `clear_secret`이면 시크릿을 지운다.
    요청이 나가는 곳(주소·엔드포인트·리전)을 바꾸면 같은 요청에 시크릿을 다시 받는다.
    저장된 시크릿이 있는데 시크릿 없이 바꾸면 422 `secret_required_for_address_change`다.
    점검 결과는 응답의 `check`와 연결 행의 마지막 점검에 남는다. 점검이 실패해도 200이다.
    """
    return await svc.update(db, conn_id, payload, user)


@router.delete("/{conn_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="연결 삭제 (super_admin)")
async def delete_connection(
    conn_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_super_admin),
) -> None:
    """연결을 삭제한다.

    캡션 품질검수에서 이 연결을 쓰는 프로젝트가 있으면 409 `connection_in_use`를 돌려준다.
    """
    await svc.delete(db, conn_id, user)

