"""호출한 사용자의 라벨링 기록(목록·상세) 조회. 그 사용자의 제출과 보유 작업만 싣는다.

기록에 넣을 작업과 상태를 정하는 규칙은 `_history_query` 한 곳에 있다.
"""
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import LabelDTO, MyWorkHistoryDetail, MyWorkHistoryRow, ReviewDTO
from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api_gateway.models import (
    Clip,
    DomainEvent,
    Label,
    Project,
    Review,
    User,
    Video,
    WorkItem,
)
from api_gateway.models.work import WORK_CLOSED
from api_gateway.services import reject_reasons
from api_gateway.services.work.service.dto import _to_dto

#: 호출한 사용자의 작업 시간이 시작되는 사건. 그 사용자가 가져온 시각과 반려로 작업이 돌아온 시각.
#: 반려는 `claimed_at`을 반려 시각으로 옮긴다(`review.decide`).
_HISTORY_START_EVENTS = ("work.claimed", "review.reject")



def _history_query(project_id: UUID, me: UUID):
    """호출한 사용자의 라벨링 기록 행 조회식을 만든다. 목록과 상세가 같이 쓴다.

    그 사용자가 낸 라벨이 있는 작업과 지금 보유한 작업을 기록에 넣는다.
    `claimed_by`만 보면 대기열로 돌아간 작업이 빠져 제출 수보다 행 수가 적어진다.
    `Label.submitted_by`는 지워지지 않고 제출 수를 세는 기준과 같다.

    `my_state`는 작업의 현재 보유자로 정한다. 닫힌 작업은 `cancelled`, 보유자가 호출한 사용자면
    작업 상태 그대로, 다른 사람이면 `handed_over`, 보유자 없이 대기열에 있으면 `returned`다.
    반려와 수정 후 승인은 `claimed_by`를 바꾸지 않는다(`review.decide`).

    `history_at`은 정렬과 커서의 기준으로, 그 사용자의 마지막 제출 시각과 지금 보유 중이면
    가져온 시각 중 늦은 값이다.
    작업의 `submitted_at`을 쓰면 넘어간 작업이 다른 사람의 제출 시각에 놓인다.

    Returns:
        (조회식, history_at 식, my_state 식). 부르는 쪽이 조건·정렬·limit을 더한다.
    """
    wi = aliased(WorkItem)
    mine = (
        select(
            Label.work_item_id.label("wid"),
            func.max(Label.submitted_at).label("my_sub"),
            func.count().label("my_count"),
        )
        .join(wi, wi.work_item_id == Label.work_item_id)
        .where(Label.submitted_by == me, wi.project_id == project_id)
        .group_by(Label.work_item_id)
        .subquery("mine")
    )
    holding = and_(WorkItem.claimed_by == me, WorkItem.status == "claimed")
    history_at = func.coalesce(
        func.greatest(mine.c.my_sub, case((holding, WorkItem.claimed_at))),
        WorkItem.created_at,
    )
    my_state = case(
        (WorkItem.status == WORK_CLOSED, WORK_CLOSED),
        (WorkItem.claimed_by == me, WorkItem.status),
        (WorkItem.claimed_by.is_not(None), "handed_over"),
        (WorkItem.status == "available", "returned"),
        else_=WorkItem.status,
    )
    stmt = (
        select(
            WorkItem,
            mine.c.my_sub,
            mine.c.my_count,
            history_at.label("history_at"),
            my_state.label("my_state"),
            Video.title,
            Clip.start_seconds,
            Clip.end_seconds,
        )
        .outerjoin(mine, mine.c.wid == WorkItem.work_item_id)
        .outerjoin(Video, Video.video_id == WorkItem.video_id)
        .outerjoin(Clip, Clip.clip_id == WorkItem.clip_id)
        .where(
            WorkItem.project_id == project_id,
            or_(WorkItem.claimed_by == me, mine.c.wid.is_not(None)),
        )
    )
    return stmt, history_at, my_state



async def _history_rows(
    db: AsyncSession, project_id: UUID, me: UUID, rows: list
) -> list[MyWorkHistoryRow]:
    """조회한 행에 호출한 사용자의 작업 시간과 그 사용자 제출의 판정을 붙인다.

    한 페이지의 작업 ID로 사건·라벨·검수를 한 번씩 읽는다. 행마다 읽으면 N+1 쿼리가 된다.

    작업 시간은 그 사용자의 마지막 제출 직전의 시작 사건(`_HISTORY_START_EVENTS`)부터 잰다.
    작업의 `claimed_at`은 작업이 넘어간 뒤 다른 사람의 값이 되므로 쓰지 않는다.
    시작 사건 기록이 없는 행은 0초 대신 None으로 둔다.

    판정은 그 사용자의 마지막 라벨을 대상으로 한 가장 최근 검수다.
    수정 후 승인은 검수자가 만든 새 라벨을 가리키므로, 부모가 그 사용자의 라벨인 검수도 포함한다.
    """
    if not rows:
        return []
    ids = [r[0].work_item_id for r in rows]
    starts: dict[UUID, list[datetime]] = {}
    for tid, at in (await db.execute(
        select(DomainEvent.target_id, DomainEvent.occurred_at).where(
            DomainEvent.target_kind == "work_item",
            DomainEvent.target_id.in_(ids),
            or_(
                and_(DomainEvent.event_type == "work.claimed", DomainEvent.actor_user_id == me),
                DomainEvent.event_type == "review.reject",
            ),
        )
    )).all():
        starts.setdefault(tid, []).append(at)

    latest_label: dict[UUID, tuple[datetime, UUID]] = {}
    for wid, lid, at in (await db.execute(
        select(Label.work_item_id, Label.label_id, Label.submitted_at).where(
            Label.work_item_id.in_(ids), Label.submitted_by == me
        )
    )).all():
        if wid not in latest_label or at > latest_label[wid][0]:
            latest_label[wid] = (at, lid)
    my_last = {lid for _, lid in latest_label.values()}
    verdicts: dict[UUID, Review] = {}
    if my_last:
        parent = aliased(Label)
        for rv, parent_id in (await db.execute(
            select(Review, parent.parent_label_id)
            .outerjoin(parent, parent.label_id == Review.label_id)
            .where(or_(Review.label_id.in_(my_last), parent.parent_label_id.in_(my_last)))
        )).all():
            key = rv.label_id if rv.label_id in my_last else parent_id
            if latest_label.get(rv.work_item_id, (None, None))[1] != key:
                continue
            seen = verdicts.get(rv.work_item_id)
            if seen is None or rv.reviewed_at > seen.reviewed_at:
                verdicts[rv.work_item_id] = rv
    catalog = None
    if any(v.reason_codes for v in verdicts.values()):
        project = await db.get(Project, project_id)
        catalog = reject_reasons.of_project(project.settings if project else None)

    out: list[MyWorkHistoryRow] = []
    for w, my_sub, my_count, history_at, my_state, title, c_start, c_end in rows:
        started = max(
            (t for t in starts.get(w.work_item_id, []) if my_sub and t <= my_sub), default=None
        )
        secs = (my_sub - started).total_seconds() if started is not None else None
        rv = verdicts.get(w.work_item_id)
        base = _to_dto(w).model_dump(exclude={"payload", "draft", "review_draft"})
        out.append(MyWorkHistoryRow(
            **base,
            payload={},
            history_at=history_at,
            my_state=my_state,
            my_submitted_at=my_sub,
            my_submit_count=my_count or 0,
            my_started_at=started,
            my_work_seconds=round(secs, 1) if secs is not None and secs > 0 else None,
            my_verdict=rv.verdict if rv else None,
            my_reasons=reject_reasons.refs(rv.reason_codes, catalog or []) if rv else [],
            video_title=title,
            clip_start_seconds=float(c_start) if c_start is not None else None,
            clip_end_seconds=float(c_end) if c_end is not None else None,
        ))
    return out



async def list_my_history(
    db: AsyncSession,
    project_id: UUID,
    user: User,
    *,
    limit: int = 50,
    before_at: datetime | None = None,
    before_id: UUID | None = None,
    q_text: str | None = None,
    state: str | None = None,
) -> list[MyWorkHistoryRow]:
    """호출한 사용자의 라벨링 기록을 최신순 커서 페이지로 돌려준다.

    행마다 작업의 현재 값과 그 사용자가 한 일(`my_*`)을 같이 싣는다.
    작업의 값만 실으면 넘어간 작업에 다른 사람의 제출 시각·상태가 표시된다.
    `(history_at, work_item_id)` 커서로 이어 받고, 검색과 상태 필터는 서버가 전체 행에 건다.

    Args:
        before_at / before_id: 앞 페이지 마지막 행의 (`history_at`, id). 둘 다 없으면 첫 페이지.
        q_text: 작업 ID 앞자리 또는 영상 제목의 일부.
        state: `MyWorkState` 값 하나. 주면 그 상태의 행만 싣는다.
    """
    me = user.user_id
    stmt, at, my_state = _history_query(project_id, me)
    if q_text and q_text.strip():
        # `_`·`%`를 이스케이프하지 않으면 앞자리 검색이 다른 행과도 일치한다.
        term = q_text.strip().lower()
        term = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(or_(
            cast(WorkItem.work_item_id, String).ilike(f"{term}%", escape="\\"),
            Video.title.ilike(f"%{term}%", escape="\\"),
        ))
    if state:
        stmt = stmt.where(my_state == state)
    if before_at is not None:
        stmt = stmt.where(
            or_(at < before_at, and_(at == before_at, WorkItem.work_item_id < before_id))
            if before_id is not None
            else at < before_at
        )
    rows = (await db.execute(
        stmt.order_by(at.desc(), WorkItem.work_item_id.desc()).limit(limit)
    )).all()
    return await _history_rows(db, project_id, me, rows)



async def my_history_detail(
    db: AsyncSession, project_id: UUID, work_item_id: UUID, user: User
) -> MyWorkHistoryDetail:
    """호출한 사용자의 라벨링 기록 한 건을 상세로 돌려준다.

    그 사용자가 낸 라벨과 그 라벨의 검수만 싣는다. 그래서 작업이 넘어간 뒤에도 기록이 열린다.
    `/work/{id}/labels`는 지금 보유한 사람과 검수자에게만 열려 있어 넘어간 작업에서 403이 되므로
    자체 쿼리로 읽는다.

    Raises:
        HTTPException: 호출한 사용자의 기록에 없는 작업인 경우(404 work_item_not_found).
            작업이 있는지도 알리지 않는다.
    """
    me = user.user_id
    stmt, _, _ = _history_query(project_id, me)
    rows = (await db.execute(stmt.where(WorkItem.work_item_id == work_item_id))).all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "work_item_not_found")
    [row] = await _history_rows(db, project_id, me, rows)

    labels = list(await db.scalars(
        select(Label)
        .where(Label.work_item_id == work_item_id, Label.submitted_by == me)
        .order_by(Label.submitted_at)
    ))
    mine = [lbl.label_id for lbl in labels]
    reviews: list[Review] = []
    if mine:
        parent = aliased(Label)
        reviews = list(await db.scalars(
            select(Review)
            .outerjoin(parent, parent.label_id == Review.label_id)
            .where(
                Review.work_item_id == work_item_id,
                or_(Review.label_id.in_(mine), parent.parent_label_id.in_(mine)),
            )
            .order_by(Review.reviewed_at)
        ))
    project = await db.get(Project, project_id)
    catalog = reject_reasons.of_project(project.settings if project else None)
    return MyWorkHistoryDetail(
        row=row,
        labels=[LabelDTO.model_validate(lbl, from_attributes=True) for lbl in labels],
        reviews=[
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
            for r in reviews
        ],
    )
