"""라벨링 작업의 조회. 대기 수·보유 수, 보유 목록, 작업 한 건·라벨 체인, 최근 품질 검사를 다룬다.

세는 조건과 보유 순서는 `classify`의 정의를 그대로 써서 화면의 수와 다음 작업 결과를 맞춘다.
"""
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import (
    LabelDTO,
    MyWorkItemDTO,
    QcRunDTO,
    RejectReasonRef,
    WorkItemDTO,
    WorkQcDTO,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Label, Project, Review, User, WorkItem
from api_gateway.services import authz, reject_reasons
from api_gateway.services.caption_qc import backlog as qc_backlog
from api_gateway.services.caption_qc import qc_finding_dto, qc_stages_dto
from api_gateway.services.work.service.classify import HELD_ORDER, _claimable_where, _held_by
from api_gateway.services.work.service.commands import _work_view_scope
from api_gateway.services.work.service.dto import _to_dto


async def count_claimable(db: AsyncSession, project_id: UUID, user: User) -> int:
    """지금 새로 배정할 수 있는 라벨링 작업 수를 돌려준다. 작업자에게는 목록 대신 이 수를 준다.

    배정 순서는 서버가 정하므로 목록에서 본 작업이 그 작업자에게 온다는 보장이 없다.
    멤버면 역할과 관계없이 볼 수 있다(`authz.require_member`).

    Raises:
        HTTPException: 멤버가 아닌 경우(403 not_a_member).
    """
    await authz.require_member(db, project_id, user)
    n = await db.scalar(
        select(func.count()).select_from(WorkItem).where(*_claimable_where(project_id))
    )
    return int(n or 0)



async def claimable_summary(
    db: AsyncSession, project_id: UUID, user: User
) -> dict[str, int]:
    """대기 수와 호출한 사용자의 보유 수를 돌려준다.

    보유 수는 `next_work`가 다시 열 작업을 찾는 조건(`_held_by`)으로 센다.
    재작업이 돌아오면 보유 수가 2일 수 있다.

    Raises:
        HTTPException: 멤버가 아닌 경우(403 not_a_member),
            프로젝트가 없는 경우(404 project_not_found).
    """
    count = await count_claimable(db, project_id, user)
    # 없는 프로젝트를 0건으로 돌려주면 같은 화면의 다른 영역과 결과가 어긋나므로 404를 낸다.
    if await db.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    held = await db.scalar(
        select(func.count()).select_from(WorkItem).where(*_held_by(project_id, user.user_id))
    )
    return {"count": count, "held": int(held or 0)}



async def list_mine(
    db: AsyncSession, user: User, *, project_id: UUID | None = None
) -> list[MyWorkItemDTO]:
    """호출한 사용자가 지금 보유 중(claimed)인 작업을 여는 순서대로 돌려준다.

    순서는 `HELD_ORDER`가 정하고, 다음 작업(`next_work`)이 다시 여는 작업이 이 목록의 첫 건이다.
    `project_id`를 주면 그 프로젝트 작업만 돌려준다.
    재작업이면 마지막 반려의 사유 코드와 이름(`last_reject_reasons`)을 붙인다.
    최신 제출 스냅숏(`payload`)은 호출한 사용자의 제출일 때만 싣는다(`get_one`과 같은 규칙).
    다른 사람이 낸 뒤 큐로 돌아온 작업의 스냅숏은 이전 사람의 캡션이기 때문이다.
    """
    q = (
        select(WorkItem)
        .where(WorkItem.claimed_by == user.user_id, WorkItem.status == "claimed")
        .order_by(*HELD_ORDER)
    )
    if project_id is not None:
        q = q.where(WorkItem.project_id == project_id)
    items = list(await db.scalars(q))
    reasons = await _last_reject_reasons(db, [w for w in items if w.rejection_count > 0])
    last_by = await _last_submitters(db, [w.work_item_id for w in items])
    out: list[MyWorkItemDTO] = []
    for w in items:
        dto = MyWorkItemDTO(
            **_to_dto(w).model_dump(), last_reject_reasons=reasons.get(w.work_item_id, []),
        )
        by = last_by.get(w.work_item_id)
        if by is not None and by != user.user_id:
            dto.payload = {}
        out.append(dto)
    return out


async def _last_submitters(db: AsyncSession, ids: list[UUID]) -> dict[UUID, UUID]:
    """작업마다 가장 최근 제출을 낸 사람을 한 번에 읽는다. 제출이 없는 작업은 빠진다."""
    if not ids:
        return {}
    rows = (await db.execute(
        select(Label.work_item_id, Label.submitted_by)
        .where(Label.work_item_id.in_(ids))
        .distinct(Label.work_item_id)
        .order_by(Label.work_item_id, Label.submitted_at.desc())
    )).all()
    return {wid: by for wid, by in rows}


async def _last_reject_reasons(
    db: AsyncSession, items: list[WorkItem]
) -> dict[UUID, list[RejectReasonRef]]:
    """작업마다 가장 최근 반려의 사유 코드와 이름을 한 번에 읽는다.

    이름은 그 작업이 속한 프로젝트의 사유 목록에서 붙인다(`reject_reasons.refs`).
    반려 누적 목록(`work.repeated_rejects`)과 같은 규칙이다.
    """
    if not items:
        return {}
    rows = (await db.execute(
        select(Review.work_item_id, Review.reason_codes)
        .where(
            Review.work_item_id.in_([w.work_item_id for w in items]),
            Review.verdict == "reject",
        )
        .distinct(Review.work_item_id)
        .order_by(Review.work_item_id, Review.reviewed_at.desc())
    )).all()
    project_of = {w.work_item_id: w.project_id for w in items}
    settings_of = dict((await db.execute(
        select(Project.project_id, Project.settings)
        .where(Project.project_id.in_(set(project_of.values())))
    )).all())
    catalogs = {pid: reject_reasons.of_project(st) for pid, st in settings_of.items()}
    return {
        wid: reject_reasons.refs(codes, catalogs.get(project_of[wid], []))
        for wid, codes in rows
    }



async def get_one(db: AsyncSession, work_item_id: UUID, user: User) -> WorkItemDTO:
    """작업 한 건을 조회한다. 본인 claim, reviewer 이상, super_admin만 볼 수 있다.

    보유자 자격으로만 보는 사용자에게는 최신 제출 스냅숏(`payload`)을
    그 사용자의 제출일 때만 싣는다.
    재투입으로 넘겨받은 작업의 스냅숏은 이전 라벨러의 제출본이다(`_work_view_scope`).

    Raises:
        HTTPException: 작업이 없는 경우(404 work_item_not_found),
            볼 권한이 없는 경우(403 not_a_member·not_reviewer).
    """
    item = await db.get(WorkItem, work_item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "work_item_not_found")
    own = await _work_view_scope(db, item, user)
    dto = _to_dto(item)
    if own is not None:
        last_by = await db.scalar(
            select(Label.submitted_by)
            .where(Label.work_item_id == work_item_id)
            .order_by(Label.submitted_at.desc())
            .limit(1)
        )
        if last_by is not None and last_by != own:
            dto.payload = {}
    return dto



async def list_labels(
    db: AsyncSession, work_item_id: UUID, user: User
) -> list[LabelDTO]:
    """작업의 라벨 revision chain을 제출 순으로 돌려준다.

    보유자, reviewer 이상 역할, super_admin이 조회할 수 있다.
    보유자 자격으로만 보는 사람은 자기 제출만 받는다(`_work_view_scope`).
    """
    item = await db.get(WorkItem, work_item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "work_item_not_found")
    own = await _work_view_scope(db, item, user)
    q = select(Label).where(Label.work_item_id == work_item_id)
    if own is not None:
        q = q.where(Label.submitted_by == own)
    rows = await db.scalars(q.order_by(Label.submitted_at))
    return [
        LabelDTO(
            label_id=lbl.label_id,
            work_item_id=lbl.work_item_id,
            parent_label_id=lbl.parent_label_id,
            submitted_by=lbl.submitted_by,
            label_data=lbl.label_data,
            diff_chars_count=lbl.diff_chars_count,
            submitted_at=lbl.submitted_at,
            ai_draft=lbl.ai_draft,
            edit_distance=lbl.edit_distance,
            draft_chars=lbl.draft_chars,
        )
        for lbl in rows
    ]



async def latest_qc(db: AsyncSession, work_item_id: UUID) -> WorkQcDTO:
    """작업의 품질검수 상태와 가장 최근 실행을 돌려준다.

    지금 버전의 검사가 아직 끝나지 않았으면 이전 버전의 실행 대신 `running`을 준다.
    재제출 직후 옛 제출본의 결과를 주면 검수자가 이미 고친 문장의 지적을 보고 판정하게 된다.
    판정은 `caption_qc.backlog.state_of` 한 곳에서 한다.
    """
    from api_gateway.models import CaptionQcRun

    item = await db.get(WorkItem, work_item_id)
    pending = await qc_backlog.state_of(db, item) if item is not None else None
    if pending is not None:
        return WorkQcDTO(state=pending)
    run = await db.scalar(
        select(CaptionQcRun)
        .where(CaptionQcRun.work_item_id == work_item_id)
        .order_by(CaptionQcRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        return WorkQcDTO(state="none")
    return WorkQcDTO(state="recorded", run=QcRunDTO(
        trigger=run.trigger, verdict=run.verdict,
        findings=[qc_finding_dto(f) for f in run.findings],
        stages=qc_stages_dto(run.stages),
        adapter_key=run.adapter_key, model=run.model, checked_at=run.created_at,
    ))
