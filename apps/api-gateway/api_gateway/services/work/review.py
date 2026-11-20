"""검수 서비스. 제출된 작업을 검수자가 승인·수정 후 승인·반려한다.

반려된 작업은 원 작업자의 재작업으로 돌아가고, 원 작업자가 없으면 대기열로 돌아간다.
반려가 여러 번 쌓인 작업은 관리자 확인 목록에 오른다(`classify.needs_admin_check_where`).
"""
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import (
    NextWorkResponse,
    ReviewDecisionRequest,
    ReviewDTO,
    WorkItemDTO,
)
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Label, Project, ProjectMember, Review, User, WorkItem
from api_gateway.services import audit, authz, caption, reject_reasons
from api_gateway.services.work import service as work_service
from api_gateway.services.work.service import (
    _human_workable,
    _next_dto,
    _to_dto,
    _work_view_scope,
    holder_left,
)

_REVIEWER_ROLES = {"reviewer", "project_admin"}


def _review_claimable_where(project_id: UUID, user: User):
    """새로 배정할 수 있는 검수 작업의 조건을 만든다. `next_review`와 대기 수 집계가 함께 쓴다.

    조건은 제출 상태, 사람 작업, 호출한 사용자가 라벨링하지 않은 작업, 검수 보유자가 없는 작업이다.
    조건을 이 함수 한 곳에 두어 대기 수와 실제로 배정할 수 있는 수를 맞춘다.

    SQL에서 `claimed_by != 사용자`는 `claimed_by`가 NULL이면 참이 되지 않는다.
    그래서 `IS NULL`을 따로 넣는다. 빼면 라벨러가 비어 있는 제출은 누구도 가져갈 수 없다.
    """
    return (
        WorkItem.project_id == project_id,
        WorkItem.status == "submitted",
        _human_workable(),
        or_(WorkItem.claimed_by.is_(None), WorkItem.claimed_by != user.user_id),
        WorkItem.review_claimed_by.is_(None),
    )


def _review_held_by(project_id: UUID, user_id: UUID):
    """호출한 사용자가 이 프로젝트에서 보유한 검수 작업의 조건을 만든다.

    다음 검수가 다시 열 작업, 보유 수, 내 검수 목록이 같은 조건을 쓴다.
    """
    return (
        WorkItem.project_id == project_id,
        WorkItem.status == "submitted",
        WorkItem.review_claimed_by == user_id,
    )


#: 검수 보유 목록의 정렬식. 검수에는 재작업 구분이 없어 가져온 순이다.
_REVIEW_HELD_ORDER = work_service.held_order(
    claimed_at=WorkItem.review_claimed_at, key=WorkItem.work_item_id,
)


async def claimable_summary(
    db: AsyncSession, project_id: UUID, user: User
) -> dict[str, int]:
    """검수 대기 수와 호출한 사용자의 검수 보유 수를 돌려준다.

    라벨링 `work/available-count`와 같은 형태로, 목록 대신 수만 돌려준다.
    멤버면 검수자가 아니어도 볼 수 있다.

    Raises:
        HTTPException: 멤버가 아닌 경우(403 not_a_member),
            프로젝트가 없는 경우(404 project_not_found).
    """
    await authz.require_member(db, project_id, user)
    # 없는 프로젝트를 0건으로 돌려주면 같은 화면의 다른 영역과 결과가 어긋나므로 404를 낸다.
    if await db.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    count = await db.scalar(
        select(func.count()).select_from(WorkItem)
        .where(*_review_claimable_where(project_id, user))
    )
    held = await db.scalar(
        select(func.count()).select_from(WorkItem)
        .where(*_review_held_by(project_id, user.user_id))
    )
    return {"count": int(count or 0), "held": int(held or 0)}


async def next_review(db: AsyncSession, project_id: UUID, user: User) -> NextWorkResponse:
    """호출한 사용자의 다음 검수 작업 한 건을 돌려준다. 보유한 검수가 있으면 그 작업을 다시 준다.

    보유한 검수가 있으면 새로 배정하지 않고 가져온 순의 첫 건을 `resumed=True`로 돌려준다.
    보유한 검수가 없을 때만 제출 순(submitted_at, work_item_id)으로 한 건을 배정한다.
    호출한 사용자가 라벨링한 작업은 제외한다.
    FOR UPDATE SKIP LOCKED로 두 검수자가 같은 작업을 가져가지 않게 한다.
    같은 사람의 동시 요청은 멤버 행 잠금으로 순서대로 처리한다(라벨링 `next_work`와 같다).

    Raises:
        HTTPException: 검수 역할이 없는 경우(403 not_reviewer),
            프로젝트가 없는 경우(404 project_not_found),
            보유한 검수도 배정할 검수도 없는 경우(404 no_review_available),
            보유한 검수가 없고 프로젝트가 멈췄거나 끝난 경우(409 project_paused·project_not_active).
    """
    await _require_reviewer(db, project_id, user)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    # 잠그지 않으면 두 번째 요청(두 번 누르기·두 탭)이 보유 없음을 보고 한 건을 더 배정한다.
    # 멤버가 아닌 super_admin은 잠글 행이 없다.
    await db.scalar(
        select(ProjectMember)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.user_id,
        )
        .with_for_update()
    )
    held = await db.scalar(
        select(WorkItem).where(*_review_held_by(project_id, user.user_id))
        .order_by(*_REVIEW_HELD_ORDER).limit(1)
    )
    if held is not None:
        await db.commit()
        return _next_dto(held, resumed=True)
    # 일시정지·종료 프로젝트는 새 배정만 막는다. 판정은 `work.service` 한 곳에서 한다.
    work_service.reject_if_not_claimable(project)
    now = datetime.now(UTC)
    item = await db.scalar(
        select(WorkItem)
        .where(*_review_claimable_where(project_id, user))
        .order_by(WorkItem.submitted_at, WorkItem.work_item_id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no_review_available")
    item.review_claimed_by = user.user_id
    item.review_claimed_at = now
    item.review_expires_at = None
    # 검수에 걸린 시간을 계산할 수 있도록 배정도 기록한다(라벨링과 같다).
    audit.emit(
        db, event_type="review.claimed", target_kind="work_item",
        target_id=item.work_item_id, actor_user_id=user.user_id,
        project_id=item.project_id, payload={},
    )
    await db.commit()
    await db.refresh(item)
    return _next_dto(item, resumed=False)


async def list_my_review(
    db: AsyncSession, project_id: UUID, user: User
) -> list[WorkItemDTO]:
    """호출한 사용자가 보유 중인 제출 상태 검수 작업을 여는 순서대로 돌려준다.

    검수 화면의 `내 작업`과 워크벤치가 쓴다. 다음 검수가 다시 여는 작업이 이 목록의 첫 건이다.
    """
    await _require_reviewer(db, project_id, user)
    rows = await db.scalars(
        select(WorkItem)
        .where(*_review_held_by(project_id, user.user_id))
        .order_by(*_REVIEW_HELD_ORDER)
    )
    return [_to_dto(w) for w in rows]


async def save_draft(
    db: AsyncSession, work_item_id: UUID, user: User, payload: dict
) -> WorkItemDTO:
    """검수 초안을 저장한다. 판정 전까지 입력한 내용을 서버에 둔다.

    지적 문장·반려 사유·수정 후 승인 캡션을 저장한다. 호출한 사용자가 보유한 작업에만 저장한다.

    판정과 같은 행 잠금으로 읽는다. 판정 요청 처리 중에 화면의 디바운스 저장이 실행되면,
    잠그지 않은 저장은 판정 전 값을 읽고 판정이 비운 초안을 되살린다.
    잠그면 저장은 판정이 끝날 때까지 기다린 뒤 제출 상태가 아니라서 409로 끝난다.

    Raises:
        HTTPException: 작업이 없는 경우(404 work_item_not_found),
            제출 상태가 아닌 경우(409 not_submitted_{status}),
            호출한 사용자의 검수 점유가 아닌 경우(403 not_your_review_claim).
    """
    item = await _lock_item(db, work_item_id)
    if item.status != "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, f"not_submitted_{item.status}")
    _require_review_holder(item, user)
    item.review_draft = payload
    await db.commit()
    await db.refresh(item)
    return _to_dto(item)


async def decide(
    db: AsyncSession, work_item_id: UUID, payload: ReviewDecisionRequest, user: User
) -> WorkItemDTO:
    """검수 판정(approve / modify_and_approve / reject)을 처리하고 WorkItemDTO를 돌려준다.

    modify_and_approve이면 새 Label revision을 추가한 뒤 approved로 바꾼다.
    수정본은 라벨러 제출과 같은 캡션 기준을 넘어야 한다.
    반려는 원 작업자에게 재작업으로 돌려주고,
    원 작업자가 프로젝트에 없으면(`holder_left`) 대기열로 돌린다.

    판정은 검수를 보유한 사람만 할 수 있고, super_admin도 면제하지 않는다(`_require_review_holder`).
    막지 않으면 옛 탭이나 API 호출로 다른 사람이 보유한 검수를 판정해 보유자의 초안이 지워진다.
    행 잠금으로 읽으므로 같은 사람의 겹친 두 번째 요청은 409로 끝나고 반려가 두 번 세어지지 않는다.

    반려 사유 칩은 `reason_codes`로, 자유 입력 문장은 `reason`으로 받는다.
    사유 코드는 반려일 때만 받고, 프로젝트 사유 목록에 있으며 켜진 코드여야 한다
    (`reject_reasons.validate_decision`).
    검증은 상태를 바꾸기 전에 한다. 뒤에서 422가 나면 일부만 바뀐 상태로 응답하기 때문이다.

    Raises:
        HTTPException: 작업이 없는 경우(404 work_item_not_found),
            검수 역할이 없는 경우(403 not_reviewer),
            자기가 라벨링한 작업인 경우(403 cannot_review_own_work),
            호출한 사용자의 검수 점유가 아닌 경우(403 not_your_review_claim),
            제출 상태가 아닌 경우(409 not_submitted_{status}),
            수정 후 승인인데 수정본이 없는 경우(422 modified_label_data_required),
            수정 후 승인인데 기준 라벨 ID가 없는 경우(422 base_label_id_required),
            기준 라벨이 이 작업의 최신 라벨이 아닌 경우(409 review_base_mismatch),
            수정본 캡션이 `caption_min_tokens`에 못 미치는 경우(422 caption_too_short).
        RequestValidationError: `reason_codes`가 목록에 없거나 꺼져 있거나,
            반려가 아닌데 온 경우, 반려에 사유 코드도 보충 설명도 없는 경우(422).
    """
    item = await _lock_item(db, work_item_id)
    await _require_reviewer(db, item.project_id, user)
    if item.status != "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, f"not_submitted_{item.status}")
    if item.claimed_by == user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot_review_own_work")
    _require_review_holder(item, user)
    project = await db.get(Project, item.project_id)
    catalog = reject_reasons.of_project(project.settings if project else None)
    codes = reject_reasons.validate_decision(
        payload.decision, payload.reason_codes, catalog, payload.reason,
    )

    # 검수 대상은 가장 최근에 제출된 Label이다.
    latest_label = await db.scalar(
        select(Label)
        .where(Label.work_item_id == item.work_item_id)
        .order_by(Label.submitted_at.desc())
        .limit(1)
    )

    if payload.decision == "modify_and_approve":
        if payload.modified_label_data is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "modified_label_data_required",
            )
        if payload.base_label_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "base_label_id_required")
    # 판정 대상은 화면이 보여 준 제출본이어야 한다. 수정본 요청에 온 `base_label_id`가
    # 최신 라벨과 다르면 다른 작업이나 옛 제출본의 캡션이 승인본이 되므로 거절한다.
    if payload.base_label_id is not None and (
        latest_label is None or payload.base_label_id != latest_label.label_id
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "review_base_mismatch")
    # 수정본도 라벨러 제출과 같은 캡션 기준(`caption.too_short`)과 같은 오류 코드를 쓴다.
    # 상태를 바꾸기 전에 검사하므로 거절해도 작업과 검수 보유가 그대로 남는다.
    if payload.decision == "modify_and_approve":
        min_tokens = project.caption_min_tokens if project else caption.DEFAULT_MIN_TOKENS
        if caption.too_short(payload.modified_label_data, min_tokens) is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "caption_too_short")

    if payload.decision == "approve":
        item.status = "approved"
    elif payload.decision == "modify_and_approve":
        # 검수자가 직접 수정하면 직전 Label을 부모로 하는 새 revision을 추가한다.
        prev = latest_label
        prev_chars = len(str(prev.label_data)) if prev else 0
        new_chars = len(str(payload.modified_label_data))
        new_label = Label(
            work_item_id=item.work_item_id,
            parent_label_id=prev.label_id if prev else None,
            submitted_by=user.user_id,  # 제출자를 검수자로 기록한다
            label_data=payload.modified_label_data,
            diff_chars_count=abs(new_chars - prev_chars),
        )
        db.add(new_label)
        await db.flush()  # label_id PK를 할당받아 Review.label_id가 참조할 수 있게 한다
        item.payload = payload.modified_label_data  # 작업 행에도 최신 스냅숏을 둔다
        item.status = "approved"
        latest_label = new_label  # 아래 Review가 새 revision을 가리키게 한다
    else:  # reject
        # 멤버에서 빠진 사람에게 재작업을 배정하면 그 사람은 작업을 열 수 없고(403)
        # 보유를 푸는 경로도 없으므로, 상태를 바꾸기 전에 원 작업자가 멤버인지 확인한다.
        gone = bool(await db.scalar(
            select(holder_left()).where(WorkItem.work_item_id == item.work_item_id)
        ))
        item.rejection_count += 1
        # 반려 맥락을 아는 원 작업자가 고치도록 `claimed_by`를 유지한 채 claimed로 바꾼다.
        # 원 작업자가 프로젝트에 없거나 `claimed_by`가 비어 있으면 대기열로 돌린다.
        # 대기열에서 다른 사람이 받으면 새 작업으로 시작한다(`next_work`).
        now = datetime.now(UTC)
        if item.claimed_by is not None and not gone:
            item.status = "claimed"
            item.claimed_at = now
            item.expires_at = None
        else:
            item.status = "available"
            # `available`인 작업에는 보유자가 없다.
            item.claimed_by = None
            item.claimed_at = None
            item.expires_at = None

    # 검수가 끝나 submitted 상태를 벗어났으므로 검수자의 배타 claim을 해제한다.
    item.review_claimed_by = None
    item.review_claimed_at = None
    item.review_expires_at = None
    item.review_draft = None  # 초안은 판정까지만 유지한다

    db.add(Review(
        work_item_id=item.work_item_id,
        label_id=latest_label.label_id if latest_label else None,
        reviewer_id=user.user_id,
        verdict=payload.decision,
        reason_codes=codes,
        comment=payload.reason,
        flagged_sentences=payload.flagged_sentences,
    ))

    decided: dict = {"reason": payload.reason, "reason_codes": codes,
                     "rejection_count": item.rejection_count}
    if payload.decision == "reject" and item.status == "available":
        # 재작업이 원 작업자 대신 대기열로 간 사실을 기록에 남긴다.
        decided["requeued"] = True
    audit.emit(
        db, event_type=f"review.{payload.decision}", target_kind="work_item",
        target_id=item.work_item_id, actor_user_id=user.user_id,
        project_id=item.project_id, payload=decided,
    )
    # 승인본 검사는 커밋 뒤 워커가 이 승인 판정을 읽어 실행하고
    # 게이트(`qc_status`)를 정한다(`caption_qc.backlog`).
    # 요청 안에서 기다리면 모델 응답을 받는 동안 행 잠금과 커넥션을 점유한다.
    await db.commit()
    await db.refresh(item)
    return _to_dto(item)


async def list_reviews(
    db: AsyncSession, work_item_id: UUID, user: User
) -> list[ReviewDTO]:
    """작업의 검수 이력을 시간순으로 돌려준다.

    반려 사유는 코드와 현재 사유 목록의 이름을 함께 싣는다. 사유는 지우지 않고 끄므로
    꺼진 사유도 이름이 온다.
    보유자 자격으로만 조회하는 사람은 자기가 제출한 라벨의 판정만 받는다(`_work_view_scope`).
    """
    item = await db.get(WorkItem, work_item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "work_item_not_found")
    own = await _work_view_scope(db, item, user)
    project = await db.get(Project, item.project_id)
    catalog = reject_reasons.of_project(project.settings if project else None)
    q = select(Review).where(Review.work_item_id == work_item_id)
    if own is not None:
        q = q.where(Review.label_id.in_(
            select(Label.label_id).where(
                Label.work_item_id == work_item_id, Label.submitted_by == own,
            )
        ))
    rows = await db.scalars(q.order_by(Review.reviewed_at))
    return [
        ReviewDTO(
            review_id=r.review_id,
            work_item_id=r.work_item_id,
            label_id=r.label_id,
            reviewer_id=r.reviewer_id,
            verdict=r.verdict,
            comment=r.comment,
            reason_codes=list(r.reason_codes or []),
            reasons=reject_reasons.refs(r.reason_codes, catalog),
            flagged_sentences=r.flagged_sentences or [],
            reviewed_at=r.reviewed_at,
        )
        for r in rows
    ]


async def _lock_item(db: AsyncSession, work_item_id: UUID) -> WorkItem:
    """작업을 행 잠금으로 읽는다. 판정·초안 저장·신고가 서로의 결과를 덮어쓰지 않게 한다.

    세 경로 모두 같은 작업의 `review_*`를 쓴다. 잠그지 않으면 늦게 커밋한 쪽이 먼저 끝난 쪽의
    결과를 덮어쓴다. 라벨링 제출과 같은 잠금(`work_service.lock_item`)을 쓴다.

    Raises:
        HTTPException: 작업이 없는 경우(404 work_item_not_found).
    """
    return await work_service.lock_item(db, work_item_id)


def _require_review_holder(item: WorkItem, user: User) -> None:
    """호출자가 검수를 보유한 사람이 아니면 거절한다. 판정과 초안 저장이 함께 쓴다.

    화면도 같은 조건(`review_claimed_by`가 현재 사용자인지)으로 읽기 전용 여부를 정한다.
    두 기준이 다르면 화면이 허용한 요청을 서버가 거절한다.

    Raises:
        HTTPException: 호출한 사용자의 검수 점유가 아닌 경우(403 not_your_review_claim).
    """
    if item.review_claimed_by != user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_your_review_claim")


async def _require_reviewer(db: AsyncSession, project_id: UUID, user: User) -> None:
    """검수 역할(reviewer·project_admin)이나 super_admin이 아니면 403을 낸다."""
    await authz.require_role(db, project_id, user, _REVIEWER_ROLES, detail="not_reviewer")
