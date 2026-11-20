"""프로젝트 역할 기반 인가 가드. 모든 도메인이 함께 쓴다.

`ProjectMember.role`(관리자·검수자·라벨러)이 이 앱 인가의 유일한 근거다.
이 모듈이 role 조회를 감싸므로
work·media·maiu·workforce·analytics 등 모든 도메인이 같은 규칙으로 권한을 검사한다.
super_admin은 프로젝트 멤버십과 무관한 전역 권한이 있으므로 역할 검사를 항상 통과한다.

호출부는 `audit.emit(...)`처럼 모듈 전체를 import해 의도를 드러낸다.

    from api_gateway.services import authz
    await authz.require_project_admin(db, project_id, user)

대상이 없으면 404, 대상은 있지만 권한이 없으면 403을 돌려준다. 대상 ID가 UUID라 추측할 수
없으므로 존재 여부는 숨기지 않는다.

`org` 도메인에 두면 가드만 쓰는 도메인(`work` 등)도 `org`를 import해야 한다.
그래서 `audit.py`와 같은 범도메인 평면 모듈로 둔다.

멤버십이 유효하려면 `status == "active"`여야 한다. 참여 기간은 두지 않는다.
DB의 `start_date`·`end_date` 컬럼은 남아 있지만 판정에 쓰지 않는다.

삭제한 프로젝트(`deleted_at`)도 이 모듈이 막는다. 삭제는 soft delete라 행이 남는다.
가드가 검사하지 않으면 목록에서만 빠지고 id로는 계속 열고 고칠 수 있다. 되살리는 경로가
없으므로 super_admin도 막는다. 응답은 없는 프로젝트와 같은 404 `project_not_found`다.

인가 가드는 읽기만 하므로 권한을 잃은 멤버가 이미 가져간 `work_item`을 바꾸지 않는다.
403을 내는 곳에서 큐를 고치면 GET 한 번이 데이터를 바꾼다. 보유를 푸는 일은 멤버 제거·나가기와
계정 정지(`work.service.release_holds`)가 맡는다.
"""
from collections.abc import Collection
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Project, ProjectMember, User


async def is_live_project(db: AsyncSession, project_id: UUID) -> bool:
    """프로젝트가 있고 삭제되지 않았으면 True를 돌려준다."""
    p = await db.get(Project, project_id)
    return p is not None and p.deleted_at is None


async def require_live_project(db: AsyncSession, project_id: UUID) -> None:
    """프로젝트가 없거나 삭제됐으면 404를 던진다. 모든 `require_*` 가드가 먼저 부른다.

    Raises:
        HTTPException: 프로젝트가 없거나 삭제된 경우(404 project_not_found).
    """
    if not await is_live_project(db, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")


async def active_membership(
    db: AsyncSession, project_id: UUID, user: User
) -> ProjectMember | None:
    """지금 유효한 멤버십을 돌려준다. `status == "active"`여야 한다.

    유효한 멤버십이 없으면 None을 돌려준다. role까지 검사하는 가드(`require_*`)와 달리
    지금 이 프로젝트의 멤버인지만 판정한다. 삭제한 프로젝트에서는 None을 돌려준다.
    """
    if not await is_live_project(db, project_id):
        return None
    m = await db.get(ProjectMember, (project_id, user.user_id))
    if not m or m.status != "active":
        return None
    return m


async def my_role(db: AsyncSession, project_id: UUID, user: User) -> str | None:
    """유효한 멤버십의 role을 돌려준다. 멤버십이 없거나 비활성이면 None을 돌려준다."""
    m = await active_membership(db, project_id, user)
    return m.role if m else None


async def require_role(
    db: AsyncSession,
    project_id: UUID,
    user: User,
    allowed: Collection[str],
    *,
    detail: str,
) -> None:
    """호출한 사용자가 `allowed` 역할의 active 멤버인지 검사한다.

    조건에 맞지 않으면 403을 던진다. super_admin은 프로젝트가 있고 삭제되지 않았으면 통과한다.

    멤버십 조건은 이 함수 한 곳에만 둔다. 도메인별 가드가 `status == "active"`를 저마다 다시
    구현하면 멤버십 조건을 바꿀 때 일부 가드만 바뀐다.

    Raises:
        HTTPException: 프로젝트가 없거나 삭제된 경우(404 project_not_found),
            역할이 맞지 않는 경우(403 `detail`).
    """
    await require_live_project(db, project_id)
    if user.global_role == "super_admin":
        return
    m = await db.get(ProjectMember, (project_id, user.user_id))
    if not m or m.status != "active" or m.role not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail)


async def require_participation(db: AsyncSession, project_id: UUID, user: User) -> None:
    """role과 무관하게 지금 참여 중인 멤버만 통과시킨다. 작업 결과를 만드는 액션에 쓴다.

    제출·임시저장처럼 본인 claim만 확인하는 액션에 쓴다.
    claim 이후 프로젝트에서 빠진 사용자의 산출물을 받으면 그 라벨의 책임자를 나중에 정할 수 없다.

    Raises:
        HTTPException: 프로젝트가 없거나 삭제된 경우(404 project_not_found),
            멤버가 아닌 경우(403 not_a_member).
    """
    await require_live_project(db, project_id)
    if user.global_role == "super_admin":
        return
    m = await db.get(ProjectMember, (project_id, user.user_id))
    if not m or m.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_a_member")


async def require_member(db: AsyncSession, project_id: UUID, user: User) -> str | None:
    """지금 이 프로젝트의 멤버인지 판정한다. 멤버면 role을 돌려준다. 멤버가 아니면 403을 던진다.

    멤버 판정을 여러 곳에서 하면 결과가 달라지므로 이 함수 한 곳에서만 한다.

    Returns:
        호출한 사용자의 프로젝트 role. super_admin은 멤버십 없이 통과하므로 `None`이다.
        role로 분기하는 호출부는 `None`을 처리해야 한다.

    Raises:
        HTTPException: 프로젝트가 없거나 삭제된 경우(404 project_not_found),
            멤버가 아닌 경우(403 not_a_member).
    """
    await require_live_project(db, project_id)
    if user.global_role == "super_admin":
        return None
    m = await db.get(ProjectMember, (project_id, user.user_id))
    if not m or m.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_a_member")
    return m.role


async def require_project_admin(db: AsyncSession, project_id: UUID, user: User) -> None:
    """super_admin과 project_admin만 통과시키는 관리 가드를 적용한다.

    설정·멤버·내보내기·통계 같은 관리 작업과 파괴적 작업(영상 삭제 등)에 쓴다.

    Raises:
        HTTPException: 관리자가 아닌 경우(403 not_project_admin).
    """
    await require_role(db, project_id, user, ("project_admin",), detail="not_project_admin")


#: 작업 흐름과 그 산출물을 볼 수 있는 역할.
#: 화면 쪽 목록은 `apps/web/src/lib/roles.ts`의 `WORK_RESULT_VIEWER_ROLES`다. 두 목록을 함께 고친다.
_WORK_RESULT_VIEWER_ROLES = ("project_admin", "reviewer")


async def is_work_result_viewer(db: AsyncSession, project_id: UUID, user: User) -> bool:
    """호출한 사용자가 작업 결과를 볼 수 있으면 True를 돌려준다.

    `require_work_result_viewer`와 같은 판정이다.

    접근은 허용하고 일부 필드만 가려야 하는 응답에 쓴다. 영상 상세(`GET /videos/{id}`)는
    라벨링 워크벤치의 재생기가 쓰므로 403을 돌려주면 작업 화면이 깨진다. 버킷 경로와 처리 이력은
    라벨링에 필요 없으므로 그 필드만 가린다.

    두 판정이 같도록 같은 상수(`_WORK_RESULT_VIEWER_ROLES`)를 읽는다.
    비활성이면 `my_role`이 `None`을 돌려주므로 여기서도 False가 된다.
    """
    if user.global_role == "super_admin":
        return await is_live_project(db, project_id)
    return (await my_role(db, project_id, user)) in _WORK_RESULT_VIEWER_ROLES


async def require_work_result_viewer(db: AsyncSession, project_id: UUID, user: User) -> None:
    """다른 사용자의 작업 결과와 영상 처리 내역을 볼 수 있는 역할만 통과시키는 가드를 적용한다.

    관리자와 검수자만 허용한다.
    완료 목록·완료 상세·영상 상세의 준비 타임라인에 쓴다.
    라벨러는 자기 큐(`내 작업`)에서 맡은 클립만 본다. 이 화면들에는 다른 사용자가 쓰고 승인된
    캡션, 버킷 키 경로, 처리 이력처럼 라벨링에 필요 없는 정보가 실린다.

    화면만 막으면 주소로 API를 직접 호출할 수 있으므로 라우터 가드(`RoleGuard`)와 함께 쓴다.

    Raises:
        HTTPException: 볼 수 있는 역할이 아닌 경우(403 not_work_result_viewer).
    """
    await require_role(
        db, project_id, user, _WORK_RESULT_VIEWER_ROLES, detail="not_work_result_viewer"
    )
