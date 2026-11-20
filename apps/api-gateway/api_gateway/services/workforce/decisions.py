"""검수자 본인이 내린 검수 판정 목록 조회.

검수자가 자기 판정을 다시 찾는 데 쓴다.
판정 목록만 돌려주고 수·비율·소요 시간 같은 지표는 만들지 않는다.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from reborn_contracts import MemberDecisionPage, MemberDecisionRow
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Clip, Project, Review, User, Video, WorkItem
from api_gateway.services import authz, reject_reasons


def _before(at_col, id_col, before_at: datetime | None, before_id: UUID | None):
    """커서 조건을 만든다. (시각, 식별자) 내림차순에서 커서 뒤의 행만 남긴다."""
    if before_at is None:
        return []
    if before_id is None:
        return [at_col < before_at]
    return [or_(at_col < before_at, and_(at_col == before_at, id_col < before_id))]


async def my_decisions(
    db: AsyncSession,
    project_id: UUID,
    user: User,
    *,
    limit: int = 50,
    before_at: datetime | None = None,
    before_id: UUID | None = None,
) -> MemberDecisionPage:
    """호출한 사용자가 이 프로젝트에서 내린 판정 목록을 최신순 커서 페이지로 돌려준다.

    `total`은 커서·페이지와 관계없이 전체 수다.
    지금 참여 중인 멤버면 역할과 관계없이 부를 수 있다. 판정이 없는 사람은 0건이다.
    경로에 사용자 ID를 받지 않으므로 다른 사람의 판정은 볼 수 없다.

    Raises:
        HTTPException: 프로젝트가 없거나 삭제된 경우(404 project_not_found),
            멤버가 아닌 경우(403).
    """
    await authz.require_member(db, project_id, user)
    project = await db.get(Project, project_id)
    return await _page(
        db, project, user.user_id, limit=limit, before_at=before_at, before_id=before_id,
    )


async def _page(
    db: AsyncSession,
    project: Project,
    user_id: UUID,
    *,
    limit: int,
    before_at: datetime | None,
    before_id: UUID | None,
) -> MemberDecisionPage:
    """한 사용자의 판정 한 페이지를 읽고 전체 수를 센다. 권한은 호출하는 함수가 먼저 확인한다."""
    project_id = project.project_id
    review_scope = (
        WorkItem.project_id == project_id,
        Review.reviewer_id == user_id,
    )

    reviews = (await db.execute(
        select(
            Review.review_id, Review.reviewed_at, Review.verdict, Review.work_item_id,
            Review.reason_codes, Review.comment, WorkItem.video_id, WorkItem.clip_id,
        )
        .join(WorkItem, WorkItem.work_item_id == Review.work_item_id)
        .where(
            *review_scope,
            *_before(Review.reviewed_at, Review.review_id, before_at, before_id),
        )
        .order_by(Review.reviewed_at.desc(), Review.review_id.desc())
        .limit(limit)
    )).all()
    catalog = reject_reasons.of_project(project.settings)
    rows = [
        MemberDecisionRow(
            decision_id=r.review_id, decided_at=r.reviewed_at,
            decision=r.verdict, video_id=r.video_id, clip_id=r.clip_id,
            work_item_id=r.work_item_id,
            reasons=reject_reasons.refs(r.reason_codes, catalog), comment=r.comment,
        )
        for r in reviews
    ]
    await _attach_targets(db, rows)

    total = int(await db.scalar(
        select(func.count()).select_from(Review)
        .join(WorkItem, WorkItem.work_item_id == Review.work_item_id).where(*review_scope)
    ) or 0)
    return MemberDecisionPage(total=total, items=rows)


async def _attach_targets(db: AsyncSession, rows: list[MemberDecisionRow]) -> None:
    """페이지 행에 영상 제목과 클립 구간을 붙인다. 행마다 조회하지 않고 한 번에 읽는다."""
    video_ids = {r.video_id for r in rows}
    clip_ids = {r.clip_id for r in rows if r.clip_id is not None}
    titles = dict((await db.execute(
        select(Video.video_id, Video.title).where(Video.video_id.in_(video_ids))
    )).all()) if video_ids else {}
    spans = {
        cid: (start, end)
        for cid, start, end in (await db.execute(
            select(Clip.clip_id, Clip.start_seconds, Clip.end_seconds)
            .where(Clip.clip_id.in_(clip_ids))
        )).all()
    } if clip_ids else {}
    for r in rows:
        r.video_title = titles.get(r.video_id)
        start, end = spans.get(r.clip_id, (None, None)) if r.clip_id else (None, None)
        r.clip_start_seconds = float(start) if start is not None else None
        r.clip_end_seconds = float(end) if end is not None else None
