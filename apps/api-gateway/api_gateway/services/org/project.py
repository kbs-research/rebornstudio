"""프로젝트 CRUD와 멤버 관리 서비스.

멤버 초대·역할 변경·제거·나가기를 맡는다.
"""
from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.exceptions import RequestValidationError
from reborn_contracts import (
    CaptionQcSettingsDTO,
    LeaveProjectResponse,
    MemberInviteRequest,
    MemberRoleUpdate,
    ProjectCreateRequest,
    ProjectDTO,
    ProjectMemberDTO,
    ProjectUpdateRequest,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import (
    Project,
    ProjectMember,
    User,
)
from api_gateway.services import (
    audit,
    authz,
    operating_time,
    platform_credentials,
    reject_reasons,
)
from api_gateway.services.analysis import presets, scene_options
from api_gateway.services.caption_qc import settings as caption_qc_settings
from api_gateway.services.org import organization as org_service
from api_gateway.services.work import service as work_service

#: 설정 원본(`settings`)과 품질검수 세부 설정을 그대로 받는 프로젝트 역할.
#: 최고관리자는 멤버가 아니어도 `role`이 project_admin으로 들어온다.
_SETTINGS_FULL_ROLES = frozenset({"project_admin"})

#: 그 밖의 역할이 받는 `settings` 키.
#: 이 역할들의 화면은 `settings`를 읽지 않아 빈 집합이다.
#: 화면이 읽을 값은 여기에 키를 더하는 대신 DTO 최상위 필드로 올린다(`caption_qc`와 같은 방식).
_SETTINGS_PUBLIC_KEYS: frozenset[str] = frozenset()


def redact_for_role(dto: ProjectDTO, role: str | None) -> ProjectDTO:
    """역할에 맞게 프로젝트 응답의 설정 값을 가린다. 관리자에게는 그대로 돌려준다.

    `settings`에는 품질검수 프롬프트, 비속어 사전, 분석 스택 식별자가 함께 저장된다.
    라벨러·검수자에게는 공개 키만 남기고, 품질검수 설정은 켜짐 여부와 검사 방식만 준다.
    역할별 판정은 이 함수 한 곳에서 한다.

    Args:
        dto: 가리기 전 프로젝트 응답.
        role: 호출자의 프로젝트 역할. 최고관리자는 project_admin이다.

    Returns:
        역할에 맞게 가린 사본. 관리자이면 받은 객체 그대로.
    """
    if role in _SETTINGS_FULL_ROLES:
        return dto
    qc = dto.caption_qc
    return dto.model_copy(update={
        "settings": {k: v for k, v in dto.settings.items() if k in _SETTINGS_PUBLIC_KEYS},
        "caption_qc": CaptionQcSettingsDTO(enabled=qc.enabled, adapter_key=qc.adapter_key),
    })


def _to_dto(
    p: Project, role: str | None = None,
) -> ProjectDTO:
    """Project 모델을 ProjectDTO로 바꾼다. `role`은 호출자의 프로젝트 내 역할이다.

    분석 스택은 화면이 dict를 직접 읽지 않도록 `settings`에서 꺼내 최상위 필드로 올린다.
    설정이 없는 프로젝트는 `of_project`가 기본값을 돌려준다.
    설정 값은 `role`에 맞게 가린다(`redact_for_role`).
    """
    preset = presets.of_project(p.settings)
    storage_ready, analysis_ready = presets.readiness(preset, p.settings)
    qcs = caption_qc_settings.of_project(p.settings)
    return redact_for_role(ProjectDTO(
        project_id=p.project_id,
        slug=p.slug,
        name=p.name,
        description=p.description,
        guide_md=p.guide_md,
        # `archived`는 `completed`와 같게 끝난 프로젝트라 한 값으로 내보낸다(`ProjectStatus`).
        status="completed" if p.status == "archived" else p.status,
        contract_deadline=p.contract_deadline,
        target_hours=p.target_hours,
        target_deident_pct=p.target_deident_pct,
        retain_original=p.retain_original,
        deident_enabled=p.deident_enabled,
        caption_min_tokens=p.caption_min_tokens,
        settings=p.settings,
        analysis_preset=preset.key,
        analysis_preset_label=preset.label,
        storage_ready=storage_ready,
        analysis_ready=analysis_ready,
        analysis_stack_missing=presets.target_missing(preset, p.settings),
        storage_stack_missing=presets.storage_target_missing(preset, p.settings),
        clip_min_seconds=scene_options.clip_min_seconds(p.settings),
        caption_qc=CaptionQcSettingsDTO(**qcs.model_dump()),
        # 목록을 정하지 않은 프로젝트는 기본 목록을 준다.
        # 화면이 `settings`를 읽어 기본값을 따로 만들지 않게 한다.
        reject_reasons=reject_reasons.of_project(p.settings),
        my_role=role,
        created_at=p.created_at,
    ), role)


def _to_member_dto(m: ProjectMember, u: User | None = None) -> ProjectMemberDTO:
    """ProjectMember와 User를 ProjectMemberDTO로 바꾼다."""
    return ProjectMemberDTO(
        project_id=m.project_id,
        user_id=m.user_id,
        role=m.role,
        status=m.status,
        joined_at=m.joined_at,
        approved_at=m.approved_at,
        email=u.email if u else None,
        display_name=u.display_name if u else None,
        privacy_consented_at=u.privacy_consented_at if u else None,
    )


#: 연락처·개인정보 필드. 멤버 목록에서 관리자에게만 싣는다.
#: 선택 항목(전화번호·생년월일·주소)은 수집하지 않으므로 멤버 응답에 아예 싣지 않는다.
_PERSONAL_FIELDS = ("email", "privacy_consented_at")


def _without_personal(dto: ProjectMemberDTO) -> ProjectMemberDTO:
    """멤버 응답에서 연락처·개인정보를 비운다. 관리자가 아닌 역할에 보내는 응답에 쓴다."""
    return dto.model_copy(update=dict.fromkeys(_PERSONAL_FIELDS))


async def create(db: AsyncSession, payload: ProjectCreateRequest, creator: User) -> ProjectDTO:
    """프로젝트를 만들고 생성자를 project_admin으로 자동 등록한 뒤 ProjectDTO를 돌려준다.

    최고관리자만 만들 수 있다. 화면에서만 막으면 API를 직접 호출해 우회할 수 있어 서버에서 확인한다.

    Raises:
        HTTPException: 만들 권한이 없는 경우(403 `not_authorized`),
            slug가 이미 있는 경우(409 `slug_already_exists`),
            분석 프리셋이 카탈로그에 없거나 쓸 수 없는 경우(400).
    """
    if creator.global_role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_authorized")
    past = _past_deadline_error(payload.contract_deadline)
    if past:
        raise RequestValidationError([past])
    org_id = await org_service.default_org_id(db)
    # 주소가 `/p/{slug}` 형식이고 화면은 slug로 찾은 첫 번째 프로젝트를 쓴다.
    # 그래서 slug는 전체에서 유일하다.
    dup = await db.scalar(select(Project).where(Project.slug == payload.slug))
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT, "slug_already_exists")

    # 사용 가능 여부는 등록한 연결로 판정한다. 방금 등록한 연결도 반영되게 DB에서 다시 읽는다.
    await platform_credentials.refresh(db)
    # 분석 스택은 생성할 때 고정하고 `update`의 허용 필드에서도 뺀다.
    # 분석된 클립이 있는 프로젝트에서 스택을 바꾸면 한 프로젝트에 두 분석 결과가 섞인다.
    preset = presets.get(payload.analysis_preset or presets.DEFAULT_KEY)
    if preset is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown_analysis_preset")
    if not preset.available:
        # 사용할 수 없는 프리셋을 받으면 등록은 성공하고 분석만 실패하는 프로젝트가 생긴다.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "analysis_preset_unavailable")

    # 클립 길이는 고정 기본값이다. 생성 때 기본값을 적어 두어 분석 잡이 같은 값을 읽게 한다.
    opts = scene_options.SceneOptions()

    project = Project(
        org_id=org_id,
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        contract_deadline=payload.contract_deadline,
        target_hours=payload.target_hours,
        settings={presets.SETTINGS_KEY: preset.key,
                  caption_qc_settings.SETTINGS_KEY: caption_qc_settings.CaptionQcSettings(
                      **(payload.caption_qc.model_dump() if payload.caption_qc else {})
                  ).model_dump(),
                  scene_options.SETTINGS_KEY: opts.model_dump()},
        created_by=creator.user_id,
    )
    db.add(project)
    await db.flush()
    db.add(ProjectMember(
        project_id=project.project_id,
        user_id=creator.user_id,
        role="project_admin",
        status="active",
        approved_by=creator.user_id,
        approved_at=datetime.now(UTC),
    ))
    audit.emit(
        db, event_type="project.created", target_kind="project",
        target_id=project.project_id, actor_user_id=creator.user_id,
        project_id=project.project_id, payload={"name": project.name, "slug": project.slug},
    )
    await db.commit()
    await db.refresh(project)
    return _to_dto(project, role="project_admin")


async def list_mine(db: AsyncSession, user: User) -> list[ProjectDTO]:
    """호출한 사용자의 프로젝트 목록을 돌려준다.

    super_admin은 전체를, 나머지는 active 멤버십의 프로젝트만 받는다.
    """
    if user.global_role == "super_admin":
        # 최고관리자는 모든 프로젝트를 project_admin 권한으로 본다.
        projects = await db.scalars(
            select(Project)
            .where(Project.deleted_at.is_(None))
            .order_by(Project.created_at.desc())
        )
        return [_to_dto(p, role="project_admin") for p in projects]

    stmt = (
        select(Project, ProjectMember.role)
        .join(ProjectMember, Project.project_id == ProjectMember.project_id)
        .where(
            ProjectMember.user_id == user.user_id,
            ProjectMember.status == "active",
            Project.deleted_at.is_(None),
        )
        .order_by(Project.created_at.desc())
    )
    result = await db.execute(stmt)
    return [_to_dto(p, role=r) for p, r in result.all()]


async def soft_delete_project(db: AsyncSession, project_id: UUID, user: User) -> None:
    """프로젝트를 soft delete한다. super_admin만 호출할 수 있다.

    삭제는 그 프로젝트의 영상·작업·결과물을 모두 목록에서 빼므로 프로젝트 관리자에게는 열지 않는다.

    Raises:
        HTTPException: 권한이 없는 경우(403 `not_super_admin`),
            프로젝트가 없는 경우(404 `project_not_found`).
    """
    if user.global_role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_super_admin")
    project = await db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    project.deleted_at = datetime.now(UTC)
    # 지운 프로젝트는 목록에서 빠지므로 화면이 ID로 이름을 찾지 못한다.
    # 그래서 이름과 주소 식별자를 이벤트 행에 남긴다.
    # 프로젝트 범위로 거를 수 있게 `project_id`도 채운다.
    audit.emit(
        db, event_type="project.deleted", target_kind="project",
        target_id=project.project_id, actor_user_id=user.user_id,
        project_id=project.project_id, payload={"name": project.name, "slug": project.slug},
    )
    await db.commit()


async def get_one(db: AsyncSession, project_id: UUID, user: User) -> ProjectDTO:
    """프로젝트 한 건을 조회한다. 멤버나 super_admin만 볼 수 있다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 `project_not_found`),
            멤버가 아닌 경우(403 `not_a_member`).
    """
    project = await db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    role = await authz.my_role(db, project_id, user)
    if role is None and user.global_role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_a_member")
    return _to_dto(project, role=role or "project_admin")


def _jsonable(v: object) -> object:
    """감사 payload에 넣을 수 있는 형태로 바꾼다. date·datetime은 ISO 문자열로 바꾼다.

    JSONB 컬럼이라 date를 그대로 넣으면 직렬화에서 실패한다.
    값은 그대로 두고 기록용 표현만 바꾼다.
    """
    return v.isoformat() if isinstance(v, date | datetime) else v


#: 비울 수 있는 필드. DB 컬럼이 NULL을 받으며, 명시적 `null`이 오면 값을 지운다.
#: 키가 `model_fields_set`에 없는 경우만 "보내지 않음"으로 읽는다.
#: `null`까지 건너뛰면 200을 주고도 옛 값이 남는다.
_CLEARABLE = frozenset({"description", "guide_md", "contract_deadline", "target_hours"})

def _past_deadline_error(deadline: date | None, current: date | None = None) -> dict | None:
    """계약 마감일이 운영 시간대의 오늘보다 앞이면 422 오류 한 건을 돌려준다. 아니면 None이다.

    이미 저장된 값과 같으면 통과시킨다. 마감이 지난 프로젝트의 다른 설정을 고칠 때
    화면이 같은 마감일을 다시 보내도 저장이 막히지 않게 한다.
    """
    if deadline is None or deadline == current or deadline >= operating_time.today():
        return None
    return {"type": "value_error", "loc": ("body", "contract_deadline"),
            "msg": "지난 날짜는 마감일로 정할 수 없습니다", "input": deadline.isoformat()}


def _reject_bad_update(payload: ProjectUpdateRequest, current_deadline: date | None) -> None:
    """상태를 바꾸기 전에 받아들일 수 없는 요청을 422로 돌려보낸다.

    비울 수 없는 필드의 `null`과 지난 날짜의 계약 마감일(`_past_deadline_error`)을 확인한다.
    `null`을 경고 없이 건너뛰면 200을 주고도 값이 그대로 남는다.

    Raises:
        RequestValidationError: 위 규칙 중 하나라도 어긴 경우(422).
            필드마다 `loc`를 지정해 화면이 그 필드 옆에 오류를 표시하게 한다.
    """
    errors: list[dict] = []
    past = _past_deadline_error(payload.contract_deadline, current_deadline)
    if past:
        errors.append(past)
    for key in sorted(payload.model_fields_set):
        if getattr(payload, key) is None and key not in _CLEARABLE:
            errors.append({"type": "missing", "loc": ("body", key),
                           "msg": "비울 수 없는 항목입니다", "input": None})
    if errors:
        raise RequestValidationError(errors)


async def update_project(
    db: AsyncSession, project_id: UUID, payload: ProjectUpdateRequest, actor: User
) -> ProjectDTO:
    """프로젝트 설정을 부분 갱신한다. project_admin이 호출한다.

    이름·설명·가이드·마감일·목표 시간·상태, 비식별화 켜기,
    캡션 품질검수 켜기·검사 방식·모델 연결을 바꿀 수 있다. 그 밖의 처리 옵션은 고정 기본값이다.
    키가 없는 필드는 바꾸지 않는다.
    키가 있고 값이 `null`이면 비울 수 있는 필드(`_CLEARABLE`)는 지우고,
    비울 수 없는 필드는 422를 낸다(`_reject_bad_update`).

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 `project_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            캡션 품질검수의 모델 연결 이름이 조직에 없는 경우(422 `connection_not_found`).
        RequestValidationError: 비울 수 없는 필드에 `null`이 왔거나
            지난 날짜를 마감일로 보낸 경우(422).
    """
    project = await db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, actor)
    _reject_bad_update(payload, project.contract_deadline)
    # 바뀐 필드별 이전 값과 새 값을 모은다. 감사 로그가 되돌릴 근거가 된다.
    changed: dict[str, dict[str, object]] = {}
    for key in (
        "name", "description", "guide_md", "contract_deadline", "target_hours",
        # 작업 운영 설정. 컬럼 이름이 payload 필드 이름과 1:1로 같다.
        "deident_enabled",
    ):
        val = getattr(payload, key)
        # `null`을 기록하는 경우는 비울 수 있는 필드에 키가 왔을 때뿐이다.
        # 다른 필드의 `null`은 `_reject_bad_update`가 이미 422로 돌려보냈다.
        # 키가 없는 필드는 건너뛴다.
        if val is not None or (key in _CLEARABLE and key in payload.model_fields_set):
            before = getattr(project, key)
            if before != val:
                changed[key] = {"from": _jsonable(before), "to": _jsonable(val)}
            setattr(project, key, val)
    if payload.caption_qc is not None:
        await caption_qc_settings.check_connection_name(
            db, project.org_id, payload.caption_qc.connection_name,
            current=caption_qc_settings.of_project(project.settings).connection_name,
        )
        # 켜기와 검사 방식, 모델 연결만 바꾼다. 프롬프트·용어집·비속어 사전은 고정 기본값(비움)이다.
        # `settings`를 통째로 바꾸면 다른 키가 사라지므로 이 키만 바꾼다.
        qc_before = caption_qc_settings.of_project(project.settings).model_dump()
        qc_after = caption_qc_settings.CaptionQcSettings(
            **payload.caption_qc.model_dump()).model_dump()
        if qc_before != qc_after:
            changed["caption_qc"] = {"from": qc_before, "to": qc_after}
        project.settings = {
            **(project.settings or {}),
            caption_qc_settings.SETTINGS_KEY: qc_after,
        }
    if payload.project_status is not None:
        if project.status != payload.project_status:
            changed["status"] = {"from": project.status, "to": payload.project_status}
        project.status = payload.project_status
    # 바뀐 필드가 없으면 기록하지 않는다.
    # 빈 `changed`가 쌓이면 감사 로그에서 실제 변경을 찾기 어렵다.
    if changed:
        audit.emit(
            db, event_type="project.updated", target_kind="project",
            target_id=project_id, actor_user_id=actor.user_id, project_id=project_id,
            payload={"changed": changed},
        )
    await db.commit()
    await db.refresh(project)
    role = await authz.my_role(db, project_id, actor)
    return _to_dto(project, role=role or "project_admin")


async def leave_project(
    db: AsyncSession, project_id: UUID, user: User, reason: str | None
) -> LeaveProjectResponse:
    """작업자 본인이 프로젝트에서 일찍 나간다.

    보유한 라벨링·검수 작업을 대기열로 돌리고(`work.service.release_holds`) 멤버십을 left로 바꾼다.
    작업은 삭제하지 않는다. member.left 감사 이벤트에 푼 작업의 ID와 수를 남긴다.

    Raises:
        HTTPException: active 멤버가 아닌 경우(403 `not_a_member`),
            남는 project_admin이 없는 경우(409 `last_admin_cannot_leave`).
    """
    member = await db.get(ProjectMember, (project_id, user.user_id))
    if member is None or member.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_a_member")
    # 마지막 관리자의 나가기는 막는다. 나가면 프로젝트를 운영할 사람이 없다.
    # 강등(`update_member_role`)·제거(`remove_member`)와 같은 가드다.
    if member.role == "project_admin":
        await _ensure_other_admin_exists(db, project_id, user.user_id)
    released = await work_service.release_holds(db, user.user_id, project_id=project_id)
    member.status = "left"
    member.leave_reason = (reason or "").strip() or None
    member.left_at = datetime.now(UTC)
    audit.emit(
        db, event_type="member.left", target_kind="project",
        target_id=project_id, actor_user_id=user.user_id, project_id=project_id,
        payload=work_service.released_payload(released),
    )
    await db.commit()
    return LeaveProjectResponse(released=sum(len(v) for v in released.values()))


async def list_members(
    db: AsyncSession, project_id: UUID, user: User, *, limit: int | None = None
) -> list[ProjectMemberDTO]:
    """프로젝트 멤버 목록을 가입 순으로 돌려준다.

    관리자와 검수자만 호출할 수 있다. 라벨러가 호출할 수 있으면 동료 전원의 이메일을 받게 된다.
    이메일과 필수 개인정보 동의 시각은 관리자에게만 싣고, 검수자에게는 그 필드를 비운 행을 준다.

    기본값은 전원이다. 멤버 화면이 이 목록을 그대로 쓰므로 기본 상한을 두면
    상한 뒤의 멤버가 표시 없이 빠진다. 멤버 행은 작아서 수천 명도 한 응답에 싣는다.
    `limit`은 적게 받으려는 호출자만 준다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 `project_not_found`),
            볼 권한이 없는 경우(403 `not_work_result_viewer`).
    """
    project = await db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_work_result_viewer(db, project_id, user)

    stmt = (
        select(ProjectMember, User)
        .join(User, ProjectMember.user_id == User.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.joined_at, ProjectMember.user_id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).all()
    dtos = [_to_member_dto(m, u) for m, u in rows]
    if user.global_role == "super_admin":
        return dtos
    if await authz.my_role(db, project_id, user) == "project_admin":
        return dtos
    return [_without_personal(d) for d in dtos]


async def update_member_role(
    db: AsyncSession,
    project_id: UUID,
    target_user_id: UUID,
    payload: MemberRoleUpdate,
    actor: User,
) -> ProjectMemberDTO:
    """active 멤버의 역할을 바꾸고 ProjectMemberDTO를 돌려준다. 마지막 관리자를 강등하면 409다.

    Raises:
        HTTPException: 관리자가 아닌 경우(403 `not_project_admin`),
            active 멤버십이 없는 경우(404 `active_membership_not_found`),
            마지막 관리자를 강등하려는 경우(409 `last_admin_cannot_leave`).
    """
    await authz.require_project_admin(db, project_id, actor)
    member = await db.get(ProjectMember, (project_id, target_user_id))
    if not member or member.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "active_membership_not_found")
    if member.role == "project_admin" and payload.role != "project_admin":
        await _ensure_other_admin_exists(db, project_id, target_user_id)
    before = member.role
    member.role = payload.role
    # 사람의 권한을 바꾸는 운영 액션이므로 `domain_events`에 남긴다.
    if before != payload.role:
        audit.emit(
            db, event_type="member.role_changed", target_kind="user",
            target_id=target_user_id, actor_user_id=actor.user_id, project_id=project_id,
            payload={"before": before, "after": payload.role},
        )
    await db.commit()
    await db.refresh(member)
    return _to_member_dto(member)


async def remove_member(
    db: AsyncSession, project_id: UUID, target_user_id: UUID, actor: User
) -> None:
    """관리자가 멤버를 제거하거나 본인이 탈퇴한다. 멤버 행을 지운다.

    화면에 남아 있는 나간(`left`)·거절된(`rejected`) 행도 지울 수 있다.
    마지막 관리자 가드는 지금 활성인 관리자에게만 적용한다.
    이미 나간 관리자 행은 지워도 활성 관리자 수가 그대로다.

    Raises:
        HTTPException: 관리자가 아닌 경우(403 `not_project_admin`),
            멤버십이 없는 경우(404 `membership_not_found`),
            마지막 활성 관리자를 제거하려는 경우(409 `last_admin_cannot_leave`).
    """
    self_leave = actor.user_id == target_user_id
    if not self_leave:
        await authz.require_project_admin(db, project_id, actor)
    member = await db.get(ProjectMember, (project_id, target_user_id))
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "membership_not_found")
    if member.role == "project_admin" and member.status == "active":
        await _ensure_other_admin_exists(db, project_id, target_user_id)
    removed_status, removed_role = member.status, member.role
    # 나가기와 같이 보유 작업을 대기열로 돌린다. 보유에는 자동 회수가 없어서
    # 풀지 않으면 작업이 멤버가 아닌 사람에게 묶인 채 남는다.
    released = await work_service.release_holds(db, target_user_id, project_id=project_id)
    await db.execute(
        delete(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == target_user_id,
        )
    )
    # 행이 사라지므로 이 이벤트가 유일한 기록이다. 제거한 사람, 대상, 제거 전 상태를 남긴다.
    audit.emit(
        db, event_type="member.removed", target_kind="user", target_id=target_user_id,
        actor_user_id=actor.user_id, project_id=project_id,
        payload={"status": removed_status, "role": removed_role, "self": self_leave,
                 **work_service.released_payload(released)},
    )
    await db.commit()


async def invite_member(
    db: AsyncSession, project_id: UUID, payload: MemberInviteRequest, inviter: User
) -> ProjectMemberDTO:
    """project_admin이 기존 사용자를 이메일로 프로젝트에 바로(active) 초대한다.

    나가기(`left`)·거절(`rejected`)·승인 대기(`pending`) 행은 지우지 않고 남으므로
    그 행을 새 초대와 같은 형태로 되살린다(`_reinvite`).
    승인 경로는 없어서 승인 대기 행도 초대로 되살린다.
    `active`·`suspended`는 이미 멤버라서 409다.

    정지된 계정은 로그인이 막히므로(`account_suspended`) 초대하지 않는다.
    없는 계정과 정지된 계정은 같은 404 `invitee_unavailable`로 응답한다. 응답이 다르면
    이메일 목록을 넣어 보는 것만으로 계정 유무와 상태를 알아낼 수 있다.
    같은 시도의 속도는 라우터의 사람별 초대 한도(`rate_limit.invite_limit`)가 제한한다.

    Args:
        project_id: 대상 프로젝트.
        payload: 초대할 사용자의 email과 role.
        inviter: 초대하는 project_admin 또는 super_admin.

    Raises:
        HTTPException: 관리자가 아닌 경우(403 `not_project_admin`),
            사용자가 없거나 사용할 수 없는 계정인 경우(404 `invitee_unavailable`),
            이미 멤버인 경우(409 `already_member`).
    """
    await authz.require_project_admin(db, project_id, inviter)
    # 이메일로 사용자를 찾는다. 대소문자를 구분하지 않는다.
    user = await db.scalar(
        select(User).where(func.lower(User.email) == payload.email.lower())
    )
    if user is None or user.approval_status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invitee_unavailable")
    existing = await db.get(ProjectMember, (project_id, user.user_id))
    if existing is not None and existing.status in _REINVITABLE:
        return await _reinvite(db, existing, user, payload, inviter)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "already_member")
    member = ProjectMember(
        project_id=project_id,
        user_id=user.user_id,
        role=payload.role,
        status="active",
        approved_by=inviter.user_id,
        approved_at=datetime.now(UTC),
    )
    db.add(member)
    audit.emit(
        db, event_type="member.invited", target_kind="user",
        target_id=user.user_id, actor_user_id=inviter.user_id, project_id=project_id,
        payload={"email": payload.email, "role": payload.role},
    )
    await db.commit()
    await db.refresh(member)
    return _to_member_dto(member, user)


#: 초대로 되살릴 수 있는 멤버십 상태. 나갔거나 거절됐거나 승인 대기인 행.
_REINVITABLE = ("left", "rejected", "pending")


async def _reinvite(
    db: AsyncSession, member: ProjectMember, user: User,
    payload: MemberInviteRequest, inviter: User,
) -> ProjectMemberDTO:
    """나간·거절된·승인 대기 멤버십 행을 새 초대처럼 되살린다. `invite_member`에서만 호출한다.

    기본키가 (프로젝트, 사용자)라서 새 행을 만들지 않는다.
    지우고 다시 넣으면 `joined_at`이 사라진다.
    역할은 이번 초대의 값을 쓴다. 이전 상태와 역할은 감사 payload에 남긴다.
    이벤트 종류는 `member.invited`이고 되살린 초대는 `reinvited`로 구분한다.
    """
    previous = {
        "previous_status": member.status,
        "previous_role": member.role,
    }
    member.status = "active"
    member.role = payload.role
    member.approved_by = inviter.user_id
    member.approved_at = datetime.now(UTC)
    member.leave_reason = None
    member.left_at = None
    audit.emit(
        db, event_type="member.invited", target_kind="user",
        target_id=member.user_id, actor_user_id=inviter.user_id,
        project_id=member.project_id,
        payload={"email": payload.email, "role": payload.role, "reinvited": True, **previous},
    )
    await db.commit()
    await db.refresh(member)
    return _to_member_dto(member, user)


async def _ensure_other_admin_exists(
    db: AsyncSession, project_id: UUID, excluding_user: UUID
) -> None:
    """excluding_user 외에 active project_admin이 남는지 확인한다. 없으면 409를 낸다."""
    other = await db.scalar(
        select(ProjectMember.user_id).where(
            ProjectMember.project_id == project_id,
            ProjectMember.role == "project_admin",
            ProjectMember.status == "active",
            ProjectMember.user_id != excluding_user,
        ).limit(1)
    )
    if other is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "last_admin_cannot_leave")


