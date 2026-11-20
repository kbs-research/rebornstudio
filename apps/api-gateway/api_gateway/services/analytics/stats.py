"""프로젝트 통계 집계. 그룹별 건수와 진척률 판정.

진척률 판정의 기준(`progress_pct`·`hours_progress`)과 모집단(`_HUMAN_WORK`)을 한 곳에 두어
개요와 설정이 같은 프로젝트를 같은 수로 표시하게 한다.
"""
import time
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import HoursProgress, ProjectStats
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway import redis_infra
from api_gateway.models import Clip, Project, ProjectMember, User, Video, WorkItem
from api_gateway.models.work import WORK_CLOSED
from api_gateway.services import authz
from api_gateway.services.media.progress import EXCLUDED_RIGHTS

# 통계 캐시(30초 TTL). 레플리카가 같은 값을 돌려주도록 Redis 공유 캐시를 쓴다.
# 인메모리 캐시는 Redis가 없는 local/test에서만 쓴다.
_CACHE_TTL_SECONDS = 30
_stats_cache: dict[UUID, tuple[float, ProjectStats]] = {}

#: 통계 캐시 키. `ProjectStats`에 필드를 더하면 버전(`v2`)을 올린다.
#: 이전 JSON을 새 모델로 읽으면 빠진 필드가 기본값으로 채워져
#: 배포 직후 30초 동안 틀린 값이 표시된다.
#: `reset_cache`의 `stats:*`는 버전과 관계없이 모두 지운다.
_CACHE_KEY = "stats:v2:{}"


def _cache_get(project_id: UUID) -> ProjectStats | None:
    """캐시를 조회한다. Redis(공유)가 있으면 Redis를, 없으면 인메모리를 쓴다."""
    r = redis_infra.get_redis()
    if r is not None:
        raw = r.get(_CACHE_KEY.format(project_id))
        return ProjectStats.model_validate_json(raw) if raw else None
    cached = _stats_cache.get(project_id)
    if cached and time.time() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    return None


def _cache_put(project_id: UUID, stats: ProjectStats) -> None:
    """캐시에 기록한다. TTL은 Redis에서는 EX로, 인메모리에서는 기록 시각으로 관리한다."""
    r = redis_infra.get_redis()
    if r is not None:
        r.set(_CACHE_KEY.format(project_id), stats.model_dump_json(), ex=_CACHE_TTL_SECONDS)
    else:
        _stats_cache[project_id] = (time.time(), stats)


def progress_pct(approved: int, total: int) -> float:
    """건수 진척률(승인 ÷ 사람 작업 × 100, 소수 1자리)을 계산한다.

    건수 진척률을 계산하는 유일한 함수다. 화면은 이 값을 그대로 표시한다.
    분모 `total`은 `_HUMAN_WORK` 모집단으로 센 수여야 한다(`for_project`).

    Returns:
        소수 1자리 백분율. 작업이 0건이면 `0.0`이다. 계약 필드가 `float`라서 `None` 대신 `0.0`을
        돌려주고, 화면은 `total == 0`일 때 `%`를 표시하지 않는다.
    """
    return round(approved / total * 100, 1) if total else 0.0


def hours_progress(
    done_hours: float, total_hours: float, target_hours: float | None
) -> HoursProgress | None:
    """시간 진척률을 계산한다. 어떤 기준으로 쟀는지(`basis`)를 값과 함께 낸다.

    개요와 통계가 같은 수를 표시하도록 기준을 고르는 판정을 이 함수 하나에 둔다.
    목표 `0`은 목표를 정하지 않은 것으로 본다.
    - 목표 시간이 있으면(0보다 크면): `basis`는 `target`(계약 목표),
      분모(`scale_hours`)는 `max(반입 분량, 목표)`다.
    - 목표가 없으면: `basis`는 `intake`(반입 분량), 분모는 반입 분량이다.

    화면의 진척 막대는 완료·반입·목표 세 구간을 한 기준에 놓으므로, 반입이 목표를 넘으면
    기준을 반입 분량으로 늘려(`max`) 막대가 넘치지 않게 한다.

    Returns:
        시간 진척률. 반입 분량이 0이면(클립이 아직 없으면) `None`이다.
        이 경우 화면은 %를 표시하지 않고 건수를 표시한다.
    """
    if total_hours <= 0:
        return None
    if target_hours:
        scale, basis = max(total_hours, target_hours), "target"
    else:
        scale, basis = total_hours, "intake"
    return HoursProgress(
        pct=round(done_hours / scale * 100, 1),
        basis=basis,
        scale_hours=round(scale, 4),
    )


def reset_cache() -> None:
    """두 백엔드의 캐시를 모두 비운다. 테스트가 부른다."""
    _stats_cache.clear()
    r = redis_infra.get_redis()
    if r is not None:
        for key in r.scan_iter(match="stats:*", count=500):
            r.delete(key)


async def for_project(db: AsyncSession, project_id: UUID, user: User) -> ProjectStats:
    """프로젝트 통계(영상·작업·멤버의 상태별 건수)를 돌려준다. `GET /projects/{id}/stats`가 부른다.

    라벨러를 뺀 프로젝트 역할만 볼 수 있다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            작업 결과를 볼 수 없는 역할인 경우(403 not_work_result_viewer).
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_work_result_viewer(db, project_id, user)
    return await compute(db, project_id)


async def compute(db: AsyncSession, project_id: UUID) -> ProjectStats:
    """프로젝트 통계를 센다. 권한을 확인하지 않으므로 권한을 이미 확인한 호출부만 부른다.

    대시보드 집계(`dashboard.queries`)가 자체 권한 확인을 거친 뒤 같은 수를 읽을 때 쓴다.
    """
    # 30초 TTL 캐시는 권한을 확인한 뒤, 결과를 계산하기 전에 조회한다.
    cached = _cache_get(project_id)
    if cached is not None:
        return cached

    # 삭제한 영상은 모든 수에서 뺀다. 같은 화면의 반입 병목·비식별화 집계도 삭제분을 빼므로
    # 여기서 넣으면 한 화면에 모집단이 둘이 된다.
    total_videos = await db.scalar(
        select(func.count()).select_from(Video)
        .where(Video.project_id == project_id, _alive_video())
    ) or 0
    work_status = await _group(db, WorkItem.status, WorkItem.project_id == project_id, _HUMAN_WORK)
    members_role = await _group(
        db, ProjectMember.role,
        ProjectMember.project_id == project_id,
        ProjectMember.status == "active",
    )
    total_clips = await db.scalar(
        select(func.count()).select_from(Clip)
        .where(Clip.project_id == project_id, _counted_clip())
    ) or 0
    work_hours = await work_hours_by_status(db, project_id)
    # 클립이 없어 시간에 잡히지 않는 작업 수. 시간 진척의 분모에서 빠진다.
    unmeasurable = await db.scalar(
        select(func.count()).select_from(WorkItem)
        .where(
            WorkItem.project_id == project_id, WorkItem.clip_id.is_(None),
            WorkItem.status != WORK_CLOSED,
        )
    ) or 0
    # 이 프로젝트에 반입된 분량(시간). 납품하기로 한 분량인 계약 목표(`target_hours`)와 다르다.
    # 비식별화 신고로 제외된 클립은 뺀다.
    # 그 클립의 작업은 닫혀 건수 분모(`_HUMAN_WORK`)에서 빠지므로
    # 분량에 남기면 시간 분모와 건수 분모가 다른 대상을 센다.
    total_hours = await db.scalar(
        select(func.coalesce(func.sum(Clip.end_seconds - Clip.start_seconds), 0) / 3600.0)
        .where(
            Clip.project_id == project_id, _counted_clip(),
            Clip.rights_status != EXCLUDED_RIGHTS,
        )
    ) or 0

    total_work = sum(work_status.values())
    result = ProjectStats(
        work_by_status=work_status,
        members_by_role=members_role,
        total_videos=total_videos,
        total_clips=total_clips,
        total_work_items=total_work,
        total_members=sum(members_role.values()),
        work_hours_by_status=work_hours,
        total_hours=round(float(total_hours), 4),
        hours_unmeasurable_count=unmeasurable,
        # 설정 화면이 다시 계산하지 않도록 서버가 같은 함수로 계산해 싣는다.
        progress_pct=progress_pct(work_status.get("approved", 0), total_work),
    )
    _cache_put(project_id, result)
    return result


#: 사람 작업 모집단 조건. 영상 단위 티켓과 끝난 작업(`cancelled`)을 뺀다.
#: 규칙은 `work.service._human_workable`과 같다. 영상 단위 work_item(clip_id IS NULL)은
#: MAIU 분석을 기다리는 디스패치 표시이고,
#: 끝난 작업은 비식별화 신고 제외나 영상 삭제로 닫힌 작업이다.
#: 둘 다 승인될 수 없어서 분모에 남기면 진척률이 100%에 닿지 못하고 큐 깊이에 `대기`로 잡힌다.
_HUMAN_WORK = and_(WorkItem.clip_id.is_not(None), WorkItem.status != WORK_CLOSED)


def _alive_video():
    """삭제되지 않은 영상 조건을 만든다. 영상·클립 집계가 함께 쓴다.

    같은 통계 화면에 모집단이 둘이 되지 않도록 모든 집계가 이 함수 하나로 삭제분을 뺀다.
    """
    return Video.deleted_at.is_(None)


def _counted_clip():
    """집계에 넣는 클립 조건을 만든다. 삭제되지 않은 영상의 클립이거나 승인된 작업이 붙은 클립이다.

    삭제한 영상의 승인분은 납품에 남고 그 시간이 `work_hours_by_status`의 `approved`에 들어간다.
    분량(`total_hours`)에서 그 클립을 빼면 완료가 분모보다 커져 진척률이 100%를 넘으므로
    납품에 남는 클립은 분모에도 남긴다.
    부르는 쿼리마다 조인 여부가 달라 상관 서브쿼리로 둔다(`_human_workable`과 같다).
    """
    return or_(
        ~exists().where((Video.video_id == Clip.video_id) & Video.deleted_at.is_not(None)),
        exists().where(
            (WorkItem.clip_id == Clip.clip_id) & (WorkItem.status == "approved")
        ),
    )


async def _group(db: AsyncSession, column, *where) -> dict[str, int]:
    """`column` 값별 건수를 `{값: 건수}` dict로 돌려준다."""
    stmt = select(column, func.count()).where(*where).group_by(column)
    return {row[0]: row[1] for row in (await db.execute(stmt)).all()}


async def work_hours_by_status(db: AsyncSession, project_id: UUID) -> dict[str, float]:
    """상태별 클립 시간 합(시간 단위)을 돌려준다. 건수 집계와 나란히 쓰도록 같은 상태 키를 쓴다.

    계약 목표가 시간 단위(`projects.target_hours`)이고 클립 길이가 제각각이라 건수 진척과 별도로
    시간 진척을 센다. 영상 단위 티켓은 건수와 시간 모두에서 빠진다(`_HUMAN_WORK`).
    두 집계는 모집단이 같아도 기준이 달라서 하나를 다른 하나의 분모로 쓰지 않는다.
    """
    stmt = (
        select(
            WorkItem.status,
            func.coalesce(func.sum(Clip.end_seconds - Clip.start_seconds), 0) / 3600.0,
        )
        .select_from(WorkItem)
        .outerjoin(Clip, Clip.clip_id == WorkItem.clip_id)
        .where(WorkItem.project_id == project_id, _HUMAN_WORK)
        .group_by(WorkItem.status)
    )
    return {st: round(float(h or 0), 4) for st, h in (await db.execute(stmt)).all()}
