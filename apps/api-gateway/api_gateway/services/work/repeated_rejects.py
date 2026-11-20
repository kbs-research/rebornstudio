"""반려가 여러 번 쌓인 미완 작업 목록.

작업 흐름은 바꾸지 않는다. 반려된 작업은 계속 만든 사람에게 재작업으로 돌아가고,
이 목록은 관리자가 가이드나 클립에 문제가 있는지 살펴볼 대상을 모은다.
대상 조건은 `classify.needs_admin_check_where` 한 곳에 둔다.
"""
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import RepeatedRejectPage, RepeatedRejectRow
from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Clip, Project, Review, User, Video, WorkItem
from api_gateway.services import authz, reject_reasons
from api_gateway.services.work.service.classify import REPEATED_REJECTS, needs_admin_check_where


async def list_for_project(
    db: AsyncSession, project_id: UUID, user: User, *, limit: int = 200
) -> RepeatedRejectPage:
    """프로젝트의 반려 누적 작업을 반려 횟수가 많은 순으로 돌려준다.

    행마다 영상 제목, 클립 구간, 보유자, 마지막 반려의 사유와 설명을 함께 싣는다.
    행마다 `/work/{id}/reviews`를 따로 부르면 목록 하나에 N+1 왕복이 되므로 한 번에 읽는다.

    Args:
        limit: 조회 상한. 화면이 상한에 닿았는지 `total`과 비교해 판별한다.

    Returns:
        실린 행과 상한과 무관하게 센 전체 수.

    Raises:
        HTTPException: 프로젝트가 없으면 404, 운영 권한이 없으면 403.
    """
    await authz.require_project_admin(db, project_id, user)
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    catalog = reject_reasons.of_project(project.settings)
    last_reject = (
        select(Review)
        .where(Review.work_item_id == WorkItem.work_item_id, Review.verdict == "reject")
        .order_by(Review.reviewed_at.desc())
        .limit(1)
        .correlate(WorkItem)
        .subquery()
        .lateral()
    )
    where = (WorkItem.project_id == project_id) & needs_admin_check_where()
    rows = (await db.execute(
        select(
            WorkItem,
            Video.title,
            Clip.start_seconds,
            Clip.end_seconds,
            User.display_name,
            last_reject.c.comment,
            last_reject.c.reason_codes,
            last_reject.c.reviewed_at,
        )
        .outerjoin(Video, Video.video_id == WorkItem.video_id)
        .outerjoin(Clip, Clip.clip_id == WorkItem.clip_id)
        .outerjoin(User, User.user_id == WorkItem.claimed_by)
        .outerjoin(last_reject, true())
        .where(where)
        .order_by(WorkItem.rejection_count.desc(), WorkItem.updated_at.desc())
        .limit(limit)
    )).all()
    items = [
        RepeatedRejectRow(
            work_item_id=w.work_item_id,
            clip_id=w.clip_id,
            status=w.status,
            video_title=title,
            clip_start_seconds=float(start) if start is not None else None,
            clip_end_seconds=float(end) if end is not None else None,
            # 대기열로 돌아간 작업에는 보유자가 없다. 남은 `claimed_by`로 이름을 붙이지 않는다.
            holder_name=holder if w.status != "available" else None,
            rejection_count=w.rejection_count,
            last_reject_reasons=reject_reasons.refs(codes, catalog),
            last_reject_comment=comment,
            last_rejected_at=reviewed_at,
        )
        for w, title, start, end, holder, comment, codes, reviewed_at in rows
    ]
    total = await db.scalar(select(func.count()).select_from(WorkItem).where(where)) or 0
    return RepeatedRejectPage(threshold=REPEATED_REJECTS, total=total, items=items)
