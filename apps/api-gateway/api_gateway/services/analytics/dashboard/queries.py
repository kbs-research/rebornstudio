"""현황·사람 지표 집계.

개요의 사업 KPI·작업 흐름·단계 배지와 통계의 사람 표가
이 모듈에 있다. 비율과 판정 조건은 `classify`에서 가져온다.
"""
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import (
    BusinessKpi,
    LabelerPerfRow,
    LabelerPerfSummary,
    PipelineSummary,
    WorkflowHealth,
)
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api_gateway.models import (
    AnalysisJob,
    Clip,
    DomainEvent,
    Label,
    Project,
    ProjectMember,
    Review,
    User,
    Video,
    WorkItem,
)
from api_gateway.services import authz
from api_gateway.services.analysis import job_state
from api_gateway.services.analytics import stats as stats_service
from api_gateway.services.analytics.dashboard.capacity import _completed_count, _deliverable_summary
from api_gateway.services.analytics.dashboard.classify import (
    _current_loads,
    _edit_rate,
    _is_reviewer_revision,
    _rate_fields,
)
from api_gateway.services.media import video as video_service
from api_gateway.services.work import service as work_service


def _decisions(project_id: UUID, since: datetime | None):
    """기간 안의 검수 판정을 판정이 속한 사람(`owner`)과 함께 고르는 select를 만든다.

    사람별 판정 수(`_labeler_counts`)와 표에 실을 사람(`_period_counts`)이 같은 select를 써서
    요약의 판정 합과 표의 합이 같게 한다.

    판정은 검수받은 라벨을 쓴 사람에게 속한다. `Review.label_id`가 비어 있는 행은
    그 work_item을 점유한 사람(`claimed_by`)에게 돌린다. 돌리지 않으면 반려가 0으로 잘못 표시된다.
    "수정 후 승인"의 `Review.label_id`는 검수자가 쓴 새 리비전을 가리키므로 부모 라벨을 쓴 사람에게
    돌린다. 그대로 세면 검수자가 많이 고친 라벨러일수록 승인율이 낮아진다.

    승인은 작업마다 첫 승인 판정 하나만 고른다. 승인된 작업은 다시 검수받지 않으므로 그 뒤의 승인
    판정은 관리자의 보류분 캡션 수정(`caption_qc.hold.revise`)이 남긴 기록이다.
    반려는 제출마다 한 번씩 나오므로 모두 고른다.

    Args:
        since: 집계 시작 시각. `Review.reviewed_at` 기준으로 자른다. 없으면 전체 기간이다.

    Returns:
        `owner`(판정이 속한 사람, 돌릴 사람이 없으면 NULL)와 `verdict` 두 열의 select.
    """
    parent = aliased(Label)
    earlier = aliased(Review)
    approving = ("approve", "modify_and_approve")
    first_approval = ~exists().where(
        earlier.work_item_id == Review.work_item_id,
        earlier.verdict.in_(approving),
        or_(
            earlier.reviewed_at < Review.reviewed_at,
            and_(earlier.reviewed_at == Review.reviewed_at, earlier.review_id < Review.review_id),
        ),
    )
    owner = func.coalesce(
        # 검수자가 쓴 리비전이면 부모 라벨을 쓴 사람에게 속한다.
        case((Label.submitted_by == Review.reviewer_id, parent.submitted_by)),
        Label.submitted_by,
        WorkItem.claimed_by,
    )
    return (
        select(owner.label("owner"), Review.verdict.label("verdict"))
        .select_from(Review)
        .outerjoin(Label, Label.label_id == Review.label_id)
        .outerjoin(parent, parent.label_id == Label.parent_label_id)
        .join(WorkItem, WorkItem.work_item_id == Review.work_item_id)
        .where(
            WorkItem.project_id == project_id,
            or_(~Review.verdict.in_(approving), first_approval),
            *([Review.reviewed_at >= since] if since else []),
        )
    )


async def _labeler_counts(
    db: AsyncSession, user_id: UUID, project_id: UUID, since: datetime | None
) -> dict[str, int]:
    """한 사람의 라벨링 제출 수와 판정 결과를 테이블(labels·reviews)에서 센다.

    이벤트(`domain_events`) 대신 테이블에서 센다. `review.*` 이벤트의 actor는 검수자라서
    이벤트로 세면 판정이 라벨러가 아닌 검수자의 수로 잡힌다. 이벤트 없이 `Review` 행만 만드는
    경로(시드 데이터, 수동 전이)도 있다.

    판정을 누구에게 돌리고 어느 승인을 세는지는 `_decisions`가 정한다.

    Args:
        since: 집계 시작 시각. 없으면 전체 기간을 센다. 제출은 `Label.submitted_at`, 판정은
            `Review.reviewed_at` 기준으로 자른다.

    Returns:
        `submitted`·`approved`·`rejected`와 수정률 재료 셋(`edit_measured`: 측정한 제출 수,
        `edit_distance`: 편집 거리 합, `draft_chars`: AI 초안 글자 수 합).
        비율은 `_edit_rate`가 계산한다.
    """
    # 제출 수와 수정률 재료를 같은 행 집합에서 한 번에 세어 기간·프로젝트 조건을 하나로 둔다.
    # 수정률은 측정한 제출(`edit_distance`가 있는 행)만 더한다(`count(열)`·`sum`은 NULL을 건너뛴다).
    # 측정하지 않은 제출을 0으로 넣으면 분모만 커져 수정률이 실제보다 낮아진다.
    submitted, edit_measured, edit_distance, draft_chars = (await db.execute(
        select(
            func.count(),
            func.count(Label.edit_distance),
            func.coalesce(func.sum(Label.edit_distance), 0),
            func.coalesce(func.sum(Label.draft_chars), 0),
        )
        .select_from(Label)
        .join(WorkItem, WorkItem.work_item_id == Label.work_item_id)
        .where(
            Label.submitted_by == user_id,
            # 검수자가 넣은 리비전은 제출로 세지 않는다. 수정 후 승인은 `submitted_by=검수자`로
            # 새 라벨을 넣으므로 세면 검수자의 제출 수가 검수량만큼 커진다.
            ~_is_reviewer_revision(),
            WorkItem.project_id == project_id,
            *([Label.submitted_at >= since] if since else []),
        )
    )).one()
    decided = _decisions(project_id, since).subquery()
    rows = (await db.execute(
        select(decided.c.verdict, func.count())
        .where(decided.c.owner == user_id)
        .group_by(decided.c.verdict)
    )).all()
    by_verdict = {v: n for v, n in rows}
    return {
        "submitted": submitted or 0,
        "approved": by_verdict.get("approve", 0) + by_verdict.get("modify_and_approve", 0),
        "rejected": by_verdict.get("reject", 0),
        "edit_measured": edit_measured or 0,
        "edit_distance": int(edit_distance or 0),
        "draft_chars": int(draft_chars or 0),
    }


async def _period_counts(
    db: AsyncSession, project_id: UUID, since: datetime | None
) -> list[tuple[User, str | None, dict[str, int]]]:
    """사람별 표에 실을 사람과 그 사람의 기간 안 수를 모은다.

    사람은 활성 멤버 전원과, 멤버가 아니지만 기간 안에 이 프로젝트에 제출했거나 판정받은
    사람이다(역할 `None`). 사람 표(`labeler_performance`)와 프로젝트 합계(`labeler_summary`)가
    같은 목록을 써서 합계와 표의 합이 같다.

    Returns:
        `(사람, 역할, _labeler_counts 결과)` 목록. 순서는 정하지 않는다.
    """
    members = (await db.execute(
        select(User, ProjectMember.role)
        .join(ProjectMember, ProjectMember.user_id == User.user_id)
        .where(ProjectMember.project_id == project_id, ProjectMember.status == "active")
    )).all()
    # 활성 멤버가 아닌데 기간 안에 제출했거나 판정받은 사람(최고관리자, 나간 멤버)도 역할 `None`으로
    # 싣는다. 빼면 표의 합이 프로젝트의 제출·판정 수보다 작아진다.
    # 나간 멤버도 그 전에 낸 작업이 기간 안에 판정될 수 있다.
    member_ids = {u.user_id for u, _role in members}
    decided = _decisions(project_id, since).subquery()
    others = (await db.scalars(
        select(User).where(or_(
            User.user_id.in_(
                select(Label.submitted_by)
                .join(WorkItem, WorkItem.work_item_id == Label.work_item_id)
                .where(
                    WorkItem.project_id == project_id,
                    ~_is_reviewer_revision(),
                    *([Label.submitted_at >= since] if since else []),
                )
            ),
            User.user_id.in_(select(decided.c.owner).where(decided.c.owner.is_not(None))),
        ))
    )).all()
    people = [*members, *((u, None) for u in others if u.user_id not in member_ids)]
    return [
        (u, role, await _labeler_counts(db, u.user_id, project_id, since)) for u, role in people
    ]


async def labeler_performance(
    db: AsyncSession, project_id: UUID, user: User, *, since: datetime | None = None
) -> list[LabelerPerfRow]:
    """사람별 작업량·반려·수정률을 제출 수 내림차순으로 돌려준다. 관리자만 볼 수 있다.

    행은 활성 멤버 전원과, 멤버가 아니지만 기간 안에 제출했거나 판정받은 사람이다(역할 `None`).

    품질 판단은 사람이 이 표의 수를 보고 하므로 이 함수는 수만 세고 평가 등급을 매기지 않는다.
    수는 이 프로젝트 안에서만 센다.

    Args:
        since: 집계 시작 시각. 그 시각 이후의 제출(`Label.submitted_at`)과
            판정(`Review.reviewed_at`)만 센다. 없으면 전체 기간을 누적한다.
            화면이 자르면 페이지에 실린 행만 세게 되므로 서버가 자른다.
    """
    await authz.require_project_admin(db, project_id, user)
    counted = await _period_counts(db, project_id, since)
    # 두 도메인(라벨링·검수)의 보유를 한 번에 모아 행마다 같은 시점의 수를 쓴다.
    label_load, review_load = await _current_loads(db, project_id)

    rows: list[LabelerPerfRow] = []
    for u, role, st in counted:
        # 최근 활동에는 기간을 적용하지 않는다. 기간 밖이라서 비워 두면 활동이 없던 사람으로 보인다.
        last = await db.scalar(
            select(func.max(DomainEvent.occurred_at)).where(
                DomainEvent.actor_user_id == u.user_id,
                DomainEvent.project_id == project_id,
            )
        )
        rows.append(LabelerPerfRow(
            user_id=u.user_id, name=u.display_name, email=u.email, role=role,
            submitted=st["submitted"], approved=st["approved"], rejected=st["rejected"],
            # 비율과 그 비율을 낸 분모를 함께 싣는다. 화면은 괄호에 이 분모를 그대로 쓴다.
            **_rate_fields(st["submitted"], st["approved"], st["rejected"]),
            # 수정률은 비율과 분모(몇 건, 몇 글자로 낸 비율인지)를 함께 싣는다.
            edit_rate=_edit_rate(st["edit_distance"], st["draft_chars"]),
            edit_measured=st["edit_measured"],
            edit_distance=st["edit_distance"],
            draft_chars=st["draft_chars"],
            last_activity=last,
            # 보유 수는 두 도메인의 합이다. 관리자는 두 도메인을 모두 맡을 수 있다.
            in_flight=label_load.get(u.user_id, 0) + review_load.get(u.user_id, 0),
        ))
    rows.sort(key=lambda r: r.submitted, reverse=True)
    return rows


def _summarize(counts: list[dict[str, int]]) -> LabelerPerfSummary:
    """사람별 수를 더해 프로젝트 합계를 만든다.

    비율은 합계에 사람별 행과 같은 규칙(`_rate_fields`·`_edit_rate`)을 적용한다.
    행별 비율의 평균은 제출 1건인 사람과 100건인 사람을 같은 무게로 세므로 쓰지 않는다.
    수가 하나도 없으면 비율은 모두 `None`이고 분모는 0이다.
    """
    def total(key: str) -> int:
        """한 수의 합을 돌려준다."""
        return sum(c[key] for c in counts)

    submitted, approved, rejected = total("submitted"), total("approved"), total("rejected")
    return LabelerPerfSummary(
        submitted=submitted,
        submitters=sum(1 for c in counts if c["submitted"] > 0),
        approved=approved,
        rejected=rejected,
        **_rate_fields(submitted, approved, rejected),
        edit_rate=_edit_rate(total("edit_distance"), total("draft_chars")),
        edit_measured=total("edit_measured"),
        edit_distance=total("edit_distance"),
        draft_chars=total("draft_chars"),
    )


async def labeler_summary(
    db: AsyncSession, project_id: UUID, user: User, *, since: datetime | None = None
) -> LabelerPerfSummary:
    """기간 안의 프로젝트 합계(제출·판정·승인율·반려율·수정률)를 돌려준다. 관리자만 볼 수 있다.

    사람별 표와 같은 사람과 같은 수(`_period_counts`)를 더하므로 합계가 같은 기간 사람별 행의
    합과 같다. 프로젝트 전체를 따로 세면 판정을 돌릴 사람이 없는 행이 합계에만 들어간다.

    Args:
        since: 집계 시작 시각. 사람별 표와 같은 기준으로 자른다. 없으면 전체 기간이다.
    """
    await authz.require_project_admin(db, project_id, user)
    return _summarize([st for _u, _role, st in await _period_counts(db, project_id, since)])


async def business_kpi(db: AsyncSession, project_id: UUID, user: User) -> BusinessKpi:
    """사업 KPI(진척)를 돌려준다.

    건수·시간 진척과 계약 목표·마감을 싣는다.
    프로젝트 전체의 진척이라 관리자와 검수자가 보고, 라벨러는 자기 큐 화면을 쓴다.
    납품 가능 요약(`deliverable`)은 비용이 큰 전수 검사라서 관리자에게만 싣는다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found),
            작업 결과를 볼 수 없는 역할인 경우(403 not_work_result_viewer).
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_work_result_viewer(db, project_id, user)
    role = None if user.global_role == "super_admin" else await authz.my_role(db, project_id, user)
    is_ops = role is None or role == "project_admin"

    ps = await stats_service.compute(db, project_id)
    work = ps.work_by_status
    approved = work.get("approved", 0)
    total = ps.total_work_items
    done_hours = round(ps.work_hours_by_status.get("approved", 0.0), 4)
    return BusinessKpi(
        # `progress_pct`는 건수 진척이다. 계산은 `stats.progress_pct` 한 곳에 있다.
        progress_pct=stats_service.progress_pct(approved, total),
        done_count=approved, total_count=total,
        done_hours=done_hours,
        total_hours=ps.total_hours,
        hours_unmeasurable_count=ps.hours_unmeasurable_count,
        # 시간 진척은 기준(분모)까지 함께 내어 개요와 통계가 같은 수를 표시하게 한다.
        hours_progress=stats_service.hours_progress(
            done_hours, ps.total_hours, project.target_hours,
        ),
        target_hours=project.target_hours, contract_deadline=project.contract_deadline,
        deliverable=(await _deliverable_summary(db, project_id)) if is_ops else None,
    )


async def workflow_health(db: AsyncSession, project_id: UUID, user: User) -> WorkflowHealth:
    """작업 흐름 상태를 돌려준다. 관리자만 볼 수 있다.

    상태별 작업 수(큐 깊이)를 싣는다.
    """
    await authz.require_project_admin(db, project_id, user)
    ps = await stats_service.compute(db, project_id)
    return WorkflowHealth(queue_depth=ps.work_by_status)


async def pipeline_summary(db: AsyncSession, project_id: UUID, user: User) -> PipelineSummary:
    """작업 단계별 대기 건수를 돌려준다. 스텝퍼와 사이드바 배지의 단일 출처다.

    관리자만 볼 수 있다.
    영상 기준 단계(변환, 분석)는 work_item에 잡히지 않아서 WorkflowHealth로 셀 수 없다.
    그래서 영상 목록의 `pipeline_stage`(services/media/video의 `_library_status`)와 같은 규칙을
    반입 분류(`_ingest_bucket_case`)로 집계해 두 화면이 같은 수를 표시하게 한다.

    수는 모두 캐시 없이 여기서 직접 센다. 캐시가 있는 집계(`stats_service.for_project`, 30초 TTL)를
    섞으면 한 응답 안에서 시점이 달라진다.
    """
    await authz.require_project_admin(db, project_id, user)

    # 영상 기준 단계는 반입 화면과 같은 분류로 센다. 반입 분류에는 우선순위가 있어
    # 분석 실패·변환 실패·규격 위반은 티켓 유무보다 먼저 `blocked`가 된다.
    # 조건을 여기서 직접 쓰면 우선순위가 어긋나 배지와 반입 화면의 수가 달라진다.
    buckets = await video_service.ingest_bucket_totals(db, project_id)

    # 이 응답의 모든 수가 같은 시점의 값이 되도록 캐시 없이 직접 센다.
    # 캐시 값과 즉시 조회가 섞이면 판정 직후 배지 하나만 바뀌어 판정이 실패한 것처럼 보인다.
    # 통계 화면보다 셀 수가 적어서 매번 세도 부하가 작다.
    async def count_work(*conds) -> int:
        """이 프로젝트의 work_item 중 조건에 맞는 수를 센다."""
        q = select(func.count()).select_from(WorkItem).where(
            WorkItem.project_id == project_id, *conds
        )
        return int(await db.scalar(q) or 0)

    # 라벨링 대기는 카드의 `대기 N`과 같은 조건(`_claimable_where`)으로 센다.
    # 클립 단위 사람 작업 중 보유자가 없는 작업이다.
    label_available = int(await db.scalar(
        select(func.count()).select_from(WorkItem)
        .where(*work_service._claimable_where(project_id))
    ) or 0)
    return PipelineSummary(
        # 비식별화 신고 목록(`work.deident_reports`)과 같은 조건으로 센다.
        # 삭제한 영상의 클립은 목록에서 빠지므로 여기서도 뺀다.
        deident_reports=int(await db.scalar(
            select(func.count()).select_from(Clip)
            .join(Video, Video.video_id == Clip.video_id)
            .where(
                Clip.project_id == project_id,
                Clip.rights_status == "legal_review",
                Video.deleted_at.is_(None),
            )
        ) or 0),
        # `ready`: 업로드·변환이 끝났고 티켓도 있어 지금 분석을 요청할 수 있는 영상.
        awaiting_analysis=buckets["ready"],
        # `preparing`: 변환·비식별화·재생본 확정 중인 영상. 사람이 할 일은 없지만
        # 대기 수가 0일 때 등록한 영상이 사라진 것처럼 보이지 않게 따로 센다.
        # 위 둘과 같은 분류의 다른 값이라 겹치지 않는다.
        preparing=buckets["preparing"],
        analyzing=int(await db.scalar(
            select(func.count()).select_from(AnalysisJob).where(
                AnalysisJob.project_id == project_id,
                # 진행 상태는 `job_state.ACTIVE`로 판정한다. `notin_(done, failed)`로 쓰면
                # 끝난 상태인 `partial`(부분 성공)이 분석 중으로 잡힌다.
                AnalysisJob.status.in_(job_state.ACTIVE),
            )
        ) or 0),
        label_available=label_available,
        review_submitted=await count_work(WorkItem.status == "submitted"),
        repeated_rejects=await count_work(work_service.needs_admin_check_where()),
        # 완료 목록 머리글의 수와 같은 정의다. `approved` 상태를 직접 세면 품질검수 보류분이 섞인다.
        completed_total=await _completed_count(db, project_id),
    )

