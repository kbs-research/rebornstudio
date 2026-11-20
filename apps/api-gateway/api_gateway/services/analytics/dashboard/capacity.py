"""처리 능력(임계 초과)과 납품 검사 분포 집계."""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import CapacityHealth, DeliverableSummary, StaleBucket
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Project, User, WorkItem
from api_gateway.services import authz
from api_gateway.services.analytics import stats as stats_service

# 납품 판정은 패키지 생성과 같은 검사(`exports`의 판정 함수)를 읽기 전용으로 쓴다.
# 여기서 사유를 다시 분류하면 화면에 표시한 사유와 패키지에서 실제로 빠지는 사유가 달라진다.
from api_gateway.services.exports import export_package, export_service

# 처리 능력은 쌓인 큐를 사람이 처리할 수 있는지 보여 준다. 라벨러를 늘려도 검수가 따라가지 못하면
# 검수 대기만 쌓이므로 단계마다 본다.

# 사람이 확인해야 하는 경과 임계 (키, 이름, 일수, 기준 시각 필드).
# 이름에 `장기`를 붙여 현황 탭의 `검수 대기`(전체 대기 수)와 구분한다.
_STALE = (
    ("review_wait", "장기 검수 대기", 3,  "submitted_at"),
    ("untouched",   "장기 미착수", 12, "created_at"),
)


async def capacity_health(db: AsyncSession, project_id: UUID, user: User) -> CapacityHealth:
    """처리 능력(임계 초과, 납품 불가 사유)을 돌려준다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found), 관리자가 아닌 경우(403).
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)

    stale = await _stale_buckets(db, project_id)
    reasons = await _blocker_reasons(db, project_id)

    return CapacityHealth(
        stale=stale,
        blocker_reasons=reasons,
        blocker_scan_limit=export_service.DELIVERABLE_SCAN_LIMIT,
    )


async def _stale_buckets(db: AsyncSession, project_id: UUID) -> list[StaleBucket]:
    """임계 일수를 넘긴 작업 수를 경과 시간의 기준 시각과 함께 센다.

    기준 시각을 함께 실어야 화면이 무엇으로부터 3일인지 표시할 수 있다.
    모집단은 현황의 대기 건수와 같은 사람 작업(`stats._HUMAN_WORK`)이다.
    `status == available`만 보면
    분석을 기다리는 영상 단위 티켓이 섞인다.
    """
    now = datetime.now(UTC)
    out: list[StaleBucket] = []
    for key, label, days, clock in _STALE:
        cutoff = now - timedelta(days=days)
        if key == "review_wait":
            where = (WorkItem.status == "submitted", WorkItem.submitted_at < cutoff)
        else:
            # 아무도 가져가지 않아 진행이 없는 작업. 실패 상태와 구분한다.
            where = (WorkItem.status == "available", WorkItem.created_at < cutoff)
        n = await db.scalar(
            select(func.count()).select_from(WorkItem)
            .where(WorkItem.project_id == project_id, stats_service._HUMAN_WORK, *where)
        ) or 0
        out.append(StaleBucket(
            key=key, label=label, threshold_days=days, count=n, clock=clock,
        ))
    return out


async def _scan_deliverable(
    db: AsyncSession, project_id: UUID
) -> tuple[dict[str, int], int] | None:
    """내보내기 후보를 납품 검사에 돌려 사유 분포와 통과 수를 함께 센다.

    납품 불가 사유와 납품 가능 수를 한 검사에서 함께 세어 두 화면의 수를 맞춘다.
    전수 검증이라서 `deliverable_total`과 같은 상한을 쓴다(`export_service`).
    상한을 넘으면 세지 않고 `None`을 돌려준다(0건과 구분한다).

    Returns:
        (사유별 건수, 통과 건수). 상한을 넘으면 `None`.
    """
    rows = (await db.execute(export_service.approved_rows_stmt(project_id))).all()
    if len(rows) > export_service.DELIVERABLE_SCAN_LIMIT:
        return None
    approved_at = await export_service._approved_at_map(db, [w.work_item_id for w, _v, _c in rows])
    reasons: dict[str, int] = {}
    ok = 0
    for w, v, c in rows:
        _m, blockers = export_package.check_row(w, v, c, approved_at.get(w.work_item_id))
        if not blockers:
            ok += 1
            continue
        # 사유가 여럿이어도 첫 사유로만 센다. 여러 사유에 세면 합이 건수를 넘는다.
        # 전체 사유는 완료 목록의 각 행에 표시된다.
        key = blockers[0].split(":", 1)[0].strip()
        reasons[key] = reasons.get(key, 0) + 1
    return reasons, ok


async def _completed_count(db: AsyncSession, project_id: UUID) -> int:
    """완료(승인) 작업 수를 센다. 완료 목록·내보내기와 같은 쿼리(`approved_rows_stmt`)를 쓴다."""
    stmt = export_service.approved_rows_stmt(project_id).order_by(None).subquery()
    return int(await db.scalar(select(func.count()).select_from(stmt)) or 0)


async def _blocker_reasons(db: AsyncSession, project_id: UUID) -> dict[str, int] | None:
    """납품 불가 사유 분포를 센다. 클립마다 첫 사유의 머리말(콜론 앞)로 묶는다.

    상한을 넘으면 `None`을 돌려준다(`_scan_deliverable`).
    """
    scan = await _scan_deliverable(db, project_id)
    return None if scan is None else scan[0]


async def _deliverable_summary(db: AsyncSession, project_id: UUID) -> DeliverableSummary | None:
    """납품 가능 건수를 사유 없이 돌려준다. 사업 KPI의 납품 가능 수가 쓴다.

    상한을 넘으면 `None`을 돌려준다(`_scan_deliverable`).
    """
    scan = await _scan_deliverable(db, project_id)
    if scan is None:
        return None
    return DeliverableSummary(count=scan[1])
