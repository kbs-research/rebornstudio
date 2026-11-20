"""비식별화 미흡 신고로 멈춘 클립의 목록과 처리.

라벨러나 검수자가 신고하면 클립이 `legal_review`가 되고 그 작업은 큐에서 빠진다
(`work.service.report_deident_miss`). 이 모듈은 관리자가 신고 내용을 보고 작업을 재개하거나
클립을 납품에서 제외하는 경로다. 판정은 사람이 하고 도구는 근거만 모아 보여 준다.
"""
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import (
    ClipDeidentReport,
    DeidentReportPage,
    DeidentReportResolveRequest,
    DeidentReportResolveResult,
    DeidentReportRow,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Clip, DomainEvent, User, Video, WorkItem
from api_gateway.services import audit, authz
from api_gateway.services.media import clip as clip_service
from api_gateway.services.work import service as work_service

#: 신고로 작업이 멈춘 클립 상태.
REPORTED = "legal_review"
#: 신고 감사 이벤트. 신고 내용은 이 이벤트에만 남는다.
REPORT_EVENT = "clip.deident_miss_reported"


async def list_for_project(
    db: AsyncSession, project_id: UUID, user: User, *, limit: int = 200,
) -> DeidentReportPage:
    """신고로 멈춘 클립을 오래된 신고부터 돌려준다. 행마다 가장 최근 신고를 싣는다.

    Args:
        limit: 조회 상한. 화면이 `total`과 비교해 상한에 닿았는지 판별한다.

    Returns:
        실린 행과 상한과 무관하게 센 전체 수.

    Raises:
        HTTPException: 운영 권한이 없는 경우(403).
    """
    await authz.require_project_admin(db, project_id, user)
    where = (
        Clip.project_id == project_id,
        Clip.rights_status == REPORTED,
        Video.deleted_at.is_(None),
    )
    rows = (await db.execute(
        select(Clip, Video.title)
        .join(Video, Video.video_id == Clip.video_id)
        .where(*where)
        .order_by(Clip.updated_at)
        .limit(limit)
    )).all()
    reports = await _latest_reports(db, [c.clip_id for c, _ in rows])
    items = [
        DeidentReportRow(
            clip_id=c.clip_id, video_id=c.video_id, video_title=title,
            start_seconds=float(c.start_seconds), end_seconds=float(c.end_seconds),
            scene_thumbnail_url=clip_service._thumbnail_url(c.extra or {}),
            report=reports.get(c.clip_id),
        )
        for c, title in rows
    ]
    total = await db.scalar(
        select(func.count()).select_from(Clip)
        .join(Video, Video.video_id == Clip.video_id).where(*where)
    ) or 0
    return DeidentReportPage(total=total, items=items)


async def _latest_reports(db: AsyncSession, clip_ids: list[UUID]) -> dict[UUID, ClipDeidentReport]:
    """클립별로 가장 최근의 신고를 신고자 이름과 함께 한 번에 읽는다.

    신고 내용은 감사 이벤트에만 남는다. 별도 컬럼을 두면 같은 사실이 두 곳에 저장된다.
    """
    if not clip_ids:
        return {}
    rows = (await db.execute(
        select(
            DomainEvent.target_id, DomainEvent.payload, DomainEvent.occurred_at, User.display_name,
        )
        .outerjoin(User, User.user_id == DomainEvent.actor_user_id)
        .where(DomainEvent.event_type == REPORT_EVENT, DomainEvent.target_id.in_(clip_ids))
        .order_by(DomainEvent.target_id, DomainEvent.occurred_at.desc())
        .distinct(DomainEvent.target_id)
    )).all()
    return {
        tid: ClipDeidentReport(
            reason=(payload or {}).get("reason"),
            timestamp_ms=(payload or {}).get("timestamp_ms"),
            reported_at=at,
            reporter_name=name,
        )
        for tid, payload, at, name in rows
    }


async def resolve(
    db: AsyncSession, clip_id: UUID, payload: DeidentReportResolveRequest, user: User,
) -> DeidentReportResolveResult:
    """신고를 처리한다. 재개하면 작업이 대기열로 돌아가고, 제외하면 클립의 미완 작업이 닫힌다.

    재개는 클립을 `auto_cleared`로 되돌린다. 작업 행은 그대로라서 클립이 허가 상태가 되면
    큐에 다시 보인다(`work.service._human_workable`). 제외는 클립을 `rejected`로 바꾸고
    미완 작업을 닫는다. 승인된 작업은 납품 이력이라 닫지 않는다.
    클립 행을 잠그므로 두 관리자가 동시에 처리해도 한 번만 반영된다.

    Raises:
        HTTPException: 클립이 없는 경우(404 clip_not_found),
            프로젝트 관리자가 아닌 경우(403), 신고 상태가 아닌 경우(409 not_reported),
            제외 사유가 없는 경우(422 exclude_note_required).
    """
    clip = await db.scalar(select(Clip).where(Clip.clip_id == clip_id).with_for_update())
    if clip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "clip_not_found")
    await authz.require_project_admin(db, clip.project_id, user)
    if clip.rights_status != REPORTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "not_reported")
    note = (payload.note or "").strip() or None
    if payload.action == "exclude" and note is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "exclude_note_required")

    clip.rights_decided_by = user.user_id
    clip.rights_decided_at = datetime.now(UTC)
    clip.rights_decision_reason = note
    resumed = closed = 0
    event_type = "clip.deident_report_resumed"
    if payload.action == "resume":
        clip.rights_status = "auto_cleared"
        resumed = await db.scalar(
            select(func.count()).select_from(WorkItem).where(
                WorkItem.clip_id == clip.clip_id, WorkItem.status.in_(work_service._CLOSABLE),
            )
        ) or 0
        # 작업이 없으면 만든다. 신고 전에 작업이 생기지 않았던 클립도 라벨링에 나가야 한다.
        await work_service.ensure_for_clip(db, clip)
    else:
        event_type = "clip.deident_report_excluded"
        clip.rights_status = "rejected"
        closed = await work_service.close_items(
            db, WorkItem.clip_id == clip.clip_id,
            reason="deident_excluded", actor_user_id=user.user_id, project_id=clip.project_id,
        )
    audit.emit(
        db, event_type=event_type, target_kind="clip", target_id=clip.clip_id,
        actor_user_id=user.user_id,
        project_id=clip.project_id,
        payload={"note": note, "resumed_work_items": resumed, "closed_work_items": closed},
    )
    await db.commit()
    return DeidentReportResolveResult(
        clip_id=clip.clip_id, rights_status=clip.rights_status,
        resumed_work_items=resumed, closed_work_items=closed,
    )
