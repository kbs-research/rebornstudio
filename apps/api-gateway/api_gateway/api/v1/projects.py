"""프로젝트 생성·조회·수정·삭제와 멤버 관리를 다루는 프로젝트 API 라우터."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from reborn_contracts import (
    AnalysisChoicesDTO,
    AnalysisPresetDTO,
    DiagnosticsReport,
    MemberInviteRequest,
    MemberRoleUpdate,
    ProjectAnalysisStackDTO,
    ProjectCreateRequest,
    ProjectDTO,
    ProjectMemberDTO,
    ProjectStats,
    ProjectStorageStackDTO,
    ProjectUpdateRequest,
    StorageChoicesDTO,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import Project, User
from api_gateway.services import authz, platform_credentials
from api_gateway.services.analysis import presets
from api_gateway.services.analytics import stats as stats_service
from api_gateway.services.auth import rate_limit
from api_gateway.services.maiu import provisioning
from api_gateway.services.org import project as project_service
from api_gateway.services.stacks import project_stack, project_storage

router = APIRouter()


@router.post(
    "",
    response_model=ProjectDTO,
    status_code=status.HTTP_201_CREATED,
    summary="프로젝트 생성 (생성자 = project_admin)",
)
async def create_project(
    payload: ProjectCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """새 프로젝트를 생성하고 생성자를 project_admin으로 등록한다."""
    return await project_service.create(db, payload, user)


# 주의: 이 경로는 `/{project_id}`보다 먼저 선언해야 한다.
# 뒤에 두면 `analysis-presets`가 project_id로 해석되어 422가 난다.
@router.get(
    "/analysis-presets",
    response_model=list[AnalysisPresetDTO],
    summary="분석 스택 카탈로그 — 프로젝트 생성 시 고른다",
    tags=["projects"],
)
async def list_analysis_presets(
    db: AsyncSession = Depends(get_db), _user: User = Depends(get_current_user),
):
    """선택할 수 있는 분석 스택 목록을 돌려준다. 쓸 수 없는 스택도 사유와 함께 싣는다.

    스택 조합은 코드가 정한다.
    운영자가 임의로 조합하면 등록은 되지만 분석이 실패하는 조합이 생긴다.
    그래서 새 조합을 추가하려면 배포가 필요하다.
    쓸 수 있는지는 등록한 연결로 판정한다. 방금 등록한 연결도 반영되게 DB에서 다시 읽는다.
    """
    await platform_credentials.refresh(db)
    return [
        AnalysisPresetDTO(
            key=p.key, label=p.label, description=p.description,
            is_default=(p.key == presets.DEFAULT_KEY),
            available=p.available, unavailable_reason=p.unavailable_reason,
            missing_providers=p.missing_providers,
            storage_key=p.storage_key, scene_adapter_key=p.scene_adapter_key,
            adapter_key=p.adapter_key,
        )
        for p in presets.all_presets()
    ]


@router.get(
    "/{project_id}/analysis-provisioning",
    response_model=DiagnosticsReport,
    summary="분석 실행 가능 여부 진단(읽기 전용)",
    tags=["projects"],
)
async def analysis_provisioning_status(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DiagnosticsReport:
    """분석을 실행할 수 있는지 워크스페이스·연동 버킷·MI 프로젝트를 확인한다.

    project_admin만 부를 수 있다. 아무것도 만들지 않으므로 화면이 자동으로 불러도 된다.
    워크스페이스와 분석 프로젝트는 운영자가 NCP 콘솔에서 만들고 분석 대상에서 고른다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found).
            project_admin이 아닌 경우(403 not_project_admin).
            분석 방식이 MI를 쓰지 않는 경우(422 analysis_stack_unsupported).
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)
    project_stack.require_supported(project.settings)
    await platform_credentials.ensure_loaded(
        db, platform_credentials.analysis_target_of(project.settings))
    checks = await provisioning.diagnose(project.settings)
    return DiagnosticsReport(
        checks=[c.__dict__ for c in checks],
        failed=sum(1 for c in checks if c.status == "fail"),
    )


@router.get(
    "/{project_id}/analysis-choices",
    response_model=AnalysisChoicesDTO,
    summary="분석 대상(MI 연결·워크스페이스·MI 프로젝트) 선택지와 잠금",
    tags=["projects"],
)
async def analysis_choices(
    project_id: UUID,
    connection_id: UUID | None = Query(
        default=None, description="워크스페이스 목록을 읽을 MI 연결. 비우면 목록을 읽지 않는다"),
    workspace: str | None = Query(
        default=None, max_length=63, description="MI 프로젝트 목록을 읽을 워크스페이스"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AnalysisChoicesDTO:
    """프로젝트의 현재 분석 대상과 고를 수 있는 연결·워크스페이스·MI 프로젝트를 돌려준다.

    project_admin만 부를 수 있다. 분석 서비스는 읽기만 한다.
    외부 조회가 실패하면 500 대신 응답의 `error`에 사유를 싣는다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            project_admin이 아닌 경우(403 not_project_admin),
            조직에 없는 MI 연결을 고른 경우(422 connection_not_found).
    """
    project = await db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)
    return await project_stack.choices(
        db, project, connection_id=connection_id, workspace=workspace)


@router.put(
    "/{project_id}/analysis-stack",
    response_model=ProjectAnalysisStackDTO,
    summary="분석 대상 저장 — 첫 영상 등록 뒤에는 바꿀 수 없다 (project_admin)",
    tags=["projects"],
)
async def save_analysis_stack(
    project_id: UUID,
    payload: ProjectAnalysisStackDTO,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectAnalysisStackDTO:
    """프로젝트의 분석 대상을 저장한다. 세 필드를 모두 비우면 기록을 지운다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            project_admin이 아닌 경우(403 not_project_admin),
            영상이 있어 바꿀 수 없는 경우(409 analysis_stack_locked),
            고르는 규칙에 맞지 않는 경우(422, `project_stack.save` 참고),
            분석 서비스를 읽지 못한 경우(503 maiu_unreachable).
    """
    project = await db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)
    return await project_stack.save(db, project, payload, user)


@router.post(
    "/{project_id}/analysis-stack/check",
    response_model=DiagnosticsReport,
    summary="분석 대상 점검 — 워크스페이스·저장소·MI 프로젝트·에셋 권한 (project_admin)",
    tags=["projects"],
)
async def check_analysis_stack(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DiagnosticsReport:
    """프로젝트의 분석 대상에 실제로 접속해 점검하고 결과를 돌려준다. 읽기만 한다.

    기록이 없으면 화면이 처음 값으로 채우는 기본값으로 점검한다.
    이 결과로 영상 등록·분석 요청 직전 점검의 캐시를 갱신한다.
    그래서 설정을 고친 뒤 점검하면 막힘이 바로 풀린다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            project_admin이 아닌 경우(403 not_project_admin),
            분석 방식이 MI를 쓰지 않는 경우(422 analysis_stack_unsupported).
    """
    project = await db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)
    project_stack.require_supported(project.settings)
    checks = await project_stack.check(db, project, fresh=True)
    return DiagnosticsReport(
        checks=[c.__dict__ for c in checks],
        failed=sum(1 for c in checks if c.status == "fail"),
    )


def _origin(request: Request) -> str | None:
    """요청한 화면의 주소(origin)를 돌려준다. 저장소 CORS 점검이 이 주소의 허용 여부를 확인한다.

    같은 주소에서 부르면 브라우저가 `Origin`을 보내지 않으므로 `Host`와 요청 scheme으로 만든다.
    scheme은 uvicorn이 신뢰 프록시의 `X-Forwarded-Proto`만 반영한 값이다. 헤더 원문은 읽지 않는다.
    """
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    host = request.headers.get("host")
    if not host:
        return None
    return f"{request.url.scheme}://{host}"


async def _admin_project(db: AsyncSession, project_id: UUID, user: User) -> Project:
    """지워지지 않은 프로젝트를 읽고 project_admin인지 확인한다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            project_admin이 아닌 경우(403 not_project_admin).
    """
    project = await db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)
    return project


@router.get(
    "/{project_id}/storage-choices",
    response_model=StorageChoicesDTO,
    summary="저장소 대상(저장소 연결·버킷) 선택지와 잠금",
    tags=["projects"],
)
async def storage_choices(
    project_id: UUID,
    connection_id: UUID | None = Query(
        default=None, description="버킷 목록을 읽을 NCP 연결. 비우면 목록을 읽지 않는다"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StorageChoicesDTO:
    """프로젝트의 현재 저장소 대상과 고를 수 있는 연결·버킷을 돌려준다.

    project_admin만 부를 수 있다. 저장소는 목록만 읽는다.
    버킷 목록을 읽지 못하면 500 대신 응답의 `error`에 사유를 싣는다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            project_admin이 아닌 경우(403 not_project_admin),
            조직에 없는 NCP 연결을 고른 경우(422 connection_not_found).
    """
    project = await _admin_project(db, project_id, user)
    return await project_storage.choices(db, project, connection_id=connection_id)


@router.put(
    "/{project_id}/storage-stack",
    response_model=ProjectStorageStackDTO,
    summary="저장소 대상 저장 — 첫 영상 등록 뒤에는 바꿀 수 없다 (project_admin)",
    tags=["projects"],
)
async def save_storage_stack(
    project_id: UUID,
    payload: ProjectStorageStackDTO,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectStorageStackDTO:
    """프로젝트의 저장소 대상을 저장한다. 두 필드를 모두 비우면 기록을 지운다.

    저장 전에 버킷 접근·목록·쓰기·읽기·삭제·업로드 주소 서명을 실제로 점검한다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            project_admin이 아닌 경우(403 not_project_admin),
            영상이 있어 바꿀 수 없는 경우(409 storage_stack_locked),
            고르는 규칙에 맞지 않는 경우(422, `project_storage.save` 참고),
            저장한 분석 대상의 연동 버킷과 다른 경우(422 maiu_bucket_mismatch),
            저장소를 읽지 못한 경우(503 storage_unreachable).
    """
    project = await _admin_project(db, project_id, user)
    return await project_stack.save_storage(db, project, payload, user, origin=_origin(request))


@router.post(
    "/{project_id}/storage-stack/check",
    response_model=DiagnosticsReport,
    summary="저장소 대상 점검 — 버킷 접근·목록·쓰기·업로드 주소·CORS (project_admin)",
    tags=["projects"],
)
async def check_storage_stack(
    project_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DiagnosticsReport:
    """프로젝트의 저장소 대상에 실제로 접속해 점검하고 결과를 돌려준다.

    점검 객체를 하나 쓰고 지운다. 기록이 없으면 화면이 처음 값으로 채우는 기본값으로 점검한다.
    이 결과로 영상 등록 직전 점검의 캐시를 갱신한다.
    그래서 설정을 고친 뒤 점검하면 막힘이 바로 풀린다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            project_admin이 아닌 경우(403 not_project_admin).
    """
    project = await _admin_project(db, project_id, user)
    checks = await project_storage.check(db, project, fresh=True, origin=_origin(request))
    return DiagnosticsReport(
        checks=[c.__dict__ for c in checks],
        failed=sum(1 for c in checks if c.status == "fail"),
    )


@router.get("", response_model=list[ProjectDTO], summary="내 프로젝트 목록")
async def list_projects(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """현재 사용자가 멤버로 속한 프로젝트 목록을 돌려준다."""
    return await project_service.list_mine(db, user)


@router.get(
    "/{project_id}/stats",
    response_model=ProjectStats,
    summary="프로젝트 통계 (라벨러 제외)",
)
async def project_stats(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """프로젝트의 영상·작업·멤버 상태별 집계 통계를 돌려준다.

    라벨러를 뺀 프로젝트 역할이 부를 수 있다.
    """
    return await stats_service.for_project(db, project_id, user)


@router.get("/{project_id}", response_model=ProjectDTO, summary="프로젝트 상세")
async def get_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """프로젝트 한 건의 상세 정보를 돌려준다. 프로젝트 멤버가 부를 수 있다."""
    return await project_service.get_one(db, project_id, user)


@router.patch(
    "/{project_id}",
    response_model=ProjectDTO,
    summary="프로젝트 설정 수정 (project_admin)",
)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """프로젝트 설정을 부분 수정한다. project_admin만 부를 수 있다.

    보내지 않은 필드는 그대로 둔다.
    `null`을 보내면 비울 수 있는 필드(`description`, `guide_md`, `contract_deadline`,
    `target_hours`)는 값을 지우고, 나머지 필드는 422를 돌려준다.
    분석 스택(`analysis_preset`)은 생성 때 고정되어 여기서 바꿀 수 없다.
    """
    return await project_service.update_project(db, project_id, payload, user)


@router.get(
    "/{project_id}/members",
    response_model=list[ProjectMemberDTO],
    summary="프로젝트 멤버 목록 (라벨러 제외)",
)
async def list_members(
    project_id: UUID,
    limit: int | None = Query(
        default=None, ge=1, description="적게 받고 싶을 때만. 안 주면 전원(조용한 상한 없음)",
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """프로젝트 멤버 전체 목록을 돌려준다. 라벨러를 뺀 프로젝트 역할이 부를 수 있다.

    `limit`을 주지 않으면 전원을 돌려준다.
    화면은 상한을 알 수 없으므로 기본 상한을 두면 상한 밖의 멤버가 목록·검색에서 표시 없이 빠진다.
    """
    return await project_service.list_members(db, project_id, user, limit=limit)


@router.patch(
    "/{project_id}/members/{user_id}/role",
    response_model=ProjectMemberDTO,
    summary="멤버 역할 변경 (project_admin)",
)
async def update_member_role(
    project_id: UUID,
    user_id: UUID,
    payload: MemberRoleUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """지정 멤버의 역할을 바꾼다(project_admin 전용)."""
    return await project_service.update_member_role(db, project_id, user_id, payload, actor)


@router.post(
    "/{project_id}/members/invite",
    response_model=ProjectMemberDTO,
    status_code=status.HTTP_200_OK,
    summary="이메일로 기존 사용자 초대 (project_admin)",
)
async def invite_member(
    project_id: UUID,
    payload: MemberInviteRequest,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """기존 가입 사용자를 이메일로 찾아 프로젝트에 즉시 active 멤버로 초대한다(project_admin).

    초대하는 사람마다 분당 한도가 있다(`rate_limit.invite_limit`). 이메일 목록을 넣어
    계정을 찾아내는 시도를 늦춘다.
    """
    await rate_limit.invite_limit(actor.user_id)
    return await project_service.invite_member(db, project_id, payload, actor)


@router.delete(
    "/{project_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="멤버 제거 / 본인 탈퇴",
)
async def remove_member(
    project_id: UUID,
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """project_admin이 멤버를 프로젝트에서 제거하거나, 본인이 프로젝트에서 나간다."""
    await project_service.remove_member(db, project_id, user_id, actor)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="프로젝트 soft delete (super_admin)",
)
async def delete_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """프로젝트를 soft delete한다(deleted_at 기록). super_admin만 부를 수 있다.

    행을 지우는 hard delete는 하지 않는다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found).
            super_admin이 아닌 경우(403 not_super_admin).
    """
    await project_service.soft_delete_project(db, project_id, user)
