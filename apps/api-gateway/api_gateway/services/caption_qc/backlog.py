"""검사를 아직 받지 못한 제출·승인을 찾는 규칙. 워커 투입·실행 직전 확인·화면 상태가 함께 쓴다.

제출과 승인은 검사를 기다리지 않고 먼저 커밋하고, 검사는 커밋 뒤 워커가 한다
(`worker_io.tasks.caption_qc_run`). 그래서 검사가 끝나지 않았다는 사실을 행 상태에서 다시 읽는다.
투입(`find_owed`), 실행 직전 확인(`is_owed`), 화면의 `검사 중`(`state_of`), 내보내기 대기
(`projects_awaiting_gate`)가 이 모듈의 조건 하나를 쓴다. 각자 조건을 적으면 화면은 `검사 중`인데
워커는 대상이 아니라고 보는 건이 생긴다.

검사 대상인지는 커밋된 사실로만 정한다.
제출 시각은 그 작업의 마지막 라벨 행(`labels.submitted_at`), 승인 시각은 마지막 승인 판정
(`reviews.reviewed_at`)이다. 그 시각 뒤에 같은 시점의 실행 기록이 없으면 검사 대기로 본다.
api가 검사 직전에 재시작돼도 제출·승인은 이미 커밋돼 있으므로 다음 주기가 다시 찾는다.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Final, Literal
from uuid import UUID

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway import redis_infra
from api_gateway.models import CaptionQcRun, DomainEvent, Label, Project, Review, WorkItem
from api_gateway.models.work import CAPTION_QC_GATE_TRIGGERS
from api_gateway.services import audit
from api_gateway.services.caption_qc import settings as qc_settings

log = logging.getLogger(__name__)

#: 워커가 검사하는 시점. `recheck`는 관리자가 화면에서 바로 실행하므로 여기에 없다.
OwedTrigger = Literal["submit", "approve"]

#: 검사 대상을 찾는 기간. 이보다 오래된 제출·승인은 다시 찾지 않는다.
#: 기간이 없으면 품질검수를 켜는 순간 그 전의 승인분 수천 건이 한꺼번에 모델 호출 대상이 된다.
#: 워커가 이 기간보다 오래 멈추면 그 사이 승인분은 검사 없이 `pending`으로 남는다.
LOOKBACK: Final = timedelta(hours=24)

#: 한 건의 검사에 허용하는 실패 횟수.
#: 모델 호출 실패는 이 횟수째 시도에서 `model_failed` 단계와 규칙 판정으로
#: 확정한다(`runner.run_owed`).
#: 실행 기록 자체를 남기지 못한 실패(DB 오류 등)는 이 횟수에 이르면 더 보내지 않아
#: 같은 원인으로 계속 실패하는 건이 모델을 끝없이 호출하지 않게 한다.
MAX_FAILURES: Final = 3

#: 실패한 검사 시도의 감사 이벤트 종류. 모델 호출 실패로 되감은 시도와 실행 기록을 남기지 못한
#: 시도를 모두 포함한다. 실패 횟수를 이 이벤트로 센다.
FAILED_EVENT: Final = "caption_qc.failed"

#: 한 건을 워커에 보낸 뒤 다시 보내지 않는 시간(초).
#: 대기열 대기와 실행 시간의 합보다 길어야 같은 건이 두 번 실행되지 않는다.
#: 모델 호출은 한 번 다시 시도하며 각 호출의 상한이 60초라 실행은 길어야 2분 남짓이다.
#: 실행이 기록을 남기거나 대상이 아니어서 끝나면 표시를 바로 지운다(`release`).
LEASE_SECONDS: Final = 300

#: 실패한 실행을 다시 보내기까지의 시간(초). 실패하면 표시 시간을 이 값으로 줄인다.
#: 모델 일시 장애가 끝나기를 기다리되, 승인분이 내보내기 대기(`export_service._QC_WAIT`) 안에
#: 확정되도록 짧게 둔다. 세 번 시도는 대략 이 값의 2배와 실행 시간의 3배를 더한 시간 안에 끝난다.
RETRY_SECONDS: Final = 60

#: 승인으로 세는 판정. 수정 후 승인도 승인본을 납품하므로 승인과 같이 센다.
_APPROVE_VERDICTS: Final = ("approve", "modify_and_approve")

#: 시점별로 작업이 있어야 하는 상태. 상태가 바뀐 작업의 이전 시점은 검사하지 않는다.
#: 제출 검사는 검수자에게 보여 줄 근거라서 판정이 끝나면 쓰지 않는다. 판정 뒤의 `payload`는
#: 검수자가 고친 버전일 수 있다. 그 값을 검사해 제출본 검사로 기록하면 기록이 틀린다.
_STATE_OF: Final[dict[str, str]] = {"submit": "submitted", "approve": "approved"}

#: 시점별로 "검사를 받았다"고 보는 실행 기록의 시점. 다시 검사도 승인본을 보므로 승인 쪽에 넣는다.
_RECORDED_BY: Final[dict[str, tuple[str, ...]]] = {
    "submit": ("submit",),
    "approve": CAPTION_QC_GATE_TRIGGERS,
}

_LEASE_KEY = "reborn:qc:lease:{work_item_id}:{trigger}"


def _anchor(trigger: str, work_item_id=WorkItem.work_item_id):
    """그 시점의 기준 시각(마지막 제출·마지막 승인)을 고르는 상관 서브쿼리를 만든다.

    제출 시각은 라벨 행에서 읽는다. 라벨 행은 사람의 제출만 만든다.
    작업 행의 `submitted_at`은 분석 회신이 영상 단위 작업을 `submitted`로 바꿀 때도 채워진다.
    그 값을 쓰면 사람이 제출하지 않은 작업까지 검사 대상이 된다.

    작업 행과의 상관은 `correlate(WorkItem)`로 명시한다. 이 서브쿼리는 `_recorded`의 EXISTS와
    `_failures`의 count 안에 한 단계 더 들어가는데, 자동 상관은 바로 바깥 SELECT만 본다.
    명시하지 않으면 `work_items`가 서브쿼리 자신의 FROM이 되어 기준 시각이 DB 전체의 마지막
    제출·승인 시각이 된다. 그러면 다른 작업의 제출 한 번에 끝난 검사가 다시 대상이 되고
    실패 횟수가 0으로 돌아간다.
    """
    if trigger == "submit":
        return (
            select(func.max(Label.submitted_at))
            .where(Label.work_item_id == work_item_id)
            .correlate(WorkItem)
            .scalar_subquery()
        )
    return (
        select(func.max(Review.reviewed_at))
        .where(Review.work_item_id == work_item_id, Review.verdict.in_(_APPROVE_VERDICTS))
        .correlate(WorkItem)
        .scalar_subquery()
    )


def _recorded(trigger: str, anchor, work_item_id=WorkItem.work_item_id):
    """기준 시각 뒤에 그 시점의 실행 기록이 있으면 참인 조건을 만든다."""
    return exists().where(
        CaptionQcRun.work_item_id == work_item_id,
        CaptionQcRun.trigger.in_(_RECORDED_BY[trigger]),
        CaptionQcRun.created_at >= anchor,
    )


def _failures(trigger: str, anchor, work_item_id=WorkItem.work_item_id):
    """기준 시각 뒤에 실패한 검사 시도(`FAILED_EVENT`)의 수를 세는 서브쿼리를 만든다."""
    return (
        select(func.count())
        .select_from(DomainEvent)
        .where(
            DomainEvent.target_kind == "work_item",
            DomainEvent.target_id == work_item_id,
            DomainEvent.event_type == FAILED_EVENT,
            DomainEvent.payload["trigger"].astext == trigger,
            DomainEvent.occurred_at >= anchor,
        )
        .scalar_subquery()
    )


def _waiting(trigger: str, now: datetime):
    """검사를 아직 받지 못한 작업의 조건을 만든다. 실패 횟수는 보지 않는다."""
    anchor = _anchor(trigger)
    conds = [
        WorkItem.status == _STATE_OF[trigger],
        anchor.is_not(None),
        anchor >= now - LOOKBACK,
        ~_recorded(trigger, anchor),
    ]
    if trigger == "approve":
        # 승인 실행이 기록되면 게이트가 `passed`나 `held`로 정해진다.
        # 이 열 조건을 먼저 걸어 승인분 대부분을 상관 서브쿼리 없이 거른다.
        conds.append(WorkItem.qc_status == "pending")
    return and_(*conds)


async def enabled_project_ids(db: AsyncSession) -> list[UUID]:
    """품질검수를 켠 프로젝트 id를 돌려준다.

    켜짐 여부는 `settings.of_project`로 읽는다. 설정 형식이 잘못된 프로젝트를 꺼짐으로 보는
    규칙이 `of_project`에 있으므로 SQL로 다시 구현하지 않는다. 프로젝트 수는 작업 수보다 훨씬
    적어 전부 읽는다.
    """
    rows = (await db.execute(select(Project.project_id, Project.settings))).all()
    return [pid for pid, st in rows if qc_settings.of_project(st).enabled]


async def find_owed(
    db: AsyncSession, *, limit: int = 50, now: datetime | None = None
) -> list[tuple[UUID, str]]:
    """워커에 보낼 `(작업 id, 시점)`을 오래 기다린 순으로 돌려준다.

    품질검수를 켠 프로젝트에서, 기간(`LOOKBACK`) 안의 제출·승인 중 그 뒤 실행 기록이 없고
    실패가 `MAX_FAILURES`에 이르지 않은 건이다.
    승인 검사가 납품 게이트를 정하므로 승인을 먼저 담는다.
    """
    now = now or datetime.now(UTC)
    projects = await enabled_project_ids(db)
    if not projects:
        return []
    out: list[tuple[UUID, str]] = []
    for trigger in ("approve", "submit"):
        anchor = _anchor(trigger)
        rows = await db.scalars(
            select(WorkItem.work_item_id)
            .where(
                WorkItem.project_id.in_(projects),
                _waiting(trigger, now),
                _failures(trigger, anchor) < MAX_FAILURES,
            )
            .order_by(anchor)
            .limit(limit - len(out))
        )
        out += [(wid, trigger) for wid in rows]
        if len(out) >= limit:
            break
    return out


async def is_owed(
    db: AsyncSession, work_item_id: UUID, trigger: str, *, now: datetime | None = None
) -> bool:
    """이 작업이 지금도 그 시점의 검사를 기다리면 참을 돌려준다. 워커가 실행 직전에 부른다.

    대기열에 있는 동안 검수자가 먼저 판정했거나, 다른 실행이 먼저 끝났거나,
    프로젝트가 품질검수를 껐을 수 있다.
    """
    if trigger not in _STATE_OF:
        return False
    now = now or datetime.now(UTC)
    item = await db.get(WorkItem, work_item_id)
    if item is None or item.project_id not in await enabled_project_ids(db):
        return False
    anchor = _anchor(trigger)
    hit = await db.scalar(
        select(WorkItem.work_item_id).where(
            WorkItem.work_item_id == work_item_id,
            _waiting(trigger, now),
            _failures(trigger, anchor) < MAX_FAILURES,
        )
    )
    return hit is not None


async def recorded_by_other(db: AsyncSession, work_item_id: UUID, trigger: str) -> bool:
    """기준 시각 뒤에 다른 실행이 이미 그 시점의 기록을 남겼으면 참을 돌려준다.

    워커가 자기 기록을 쓰기 전에 작업 행을 잠근 채 부른다.
    같은 건이 두 번 실행되어도 기록은 하나만 남는다.
    """
    anchor = _anchor(trigger, work_item_id)
    hit = await db.scalar(
        select(CaptionQcRun.run_id).where(
            CaptionQcRun.work_item_id == work_item_id,
            CaptionQcRun.trigger.in_(_RECORDED_BY[trigger]),
            CaptionQcRun.created_at >= anchor,
        ).limit(1)
    )
    return hit is not None


async def failures_of(db: AsyncSession, work_item_id: UUID, trigger: str) -> int:
    """기준 시각 뒤에 이 작업의 그 시점 검사가 실패한 횟수를 돌려준다."""
    anchor = _anchor(trigger)
    n = await db.scalar(
        select(_failures(trigger, anchor)).where(WorkItem.work_item_id == work_item_id)
    )
    return int(n or 0)


QcState = Literal["running", "failed"]


async def state_of(
    db: AsyncSession, item: WorkItem, *, now: datetime | None = None
) -> QcState | None:
    """현재 버전의 검사가 끝나지 않았으면 그 상태를, 끝났거나 대상이 아니면 `None`을 돌려준다.

    `running`은 워커가 곧 검사하거나 검사하고 있다는 뜻이다.
    `failed`는 실행 기록을 남기지 못한 채 `MAX_FAILURES`번 실패해 더 시도하지 않는다는 뜻이다.
    `None`이면 최신 실행 기록이 현재 버전의 결과이거나 검사 대상이 아닌 작업이다.
    """
    trigger = {v: k for k, v in _STATE_OF.items()}.get(item.status)
    if trigger is None or item.project_id not in await enabled_project_ids(db):
        return None
    now = now or datetime.now(UTC)
    anchor = _anchor(trigger)
    fails = await db.scalar(
        select(_failures(trigger, anchor)).where(
            WorkItem.work_item_id == item.work_item_id, _waiting(trigger, now),
        )
    )
    if fails is None:
        return None
    return "failed" if fails >= MAX_FAILURES else "running"


async def running_ids(
    db: AsyncSession, ids: list[UUID], *, now: datetime | None = None
) -> set[UUID]:
    """주어진 승인분 중 승인 검사를 기다리는 작업 id를 돌려준다. 완료 목록의 `검사 중` 표시에 쓴다.

    실패 횟수가 `MAX_FAILURES`에 이른 건은 더 검사하지 않으므로 뺀다.
    """
    if not ids:
        return set()
    projects = await enabled_project_ids(db)
    if not projects:
        return set()
    now = now or datetime.now(UTC)
    anchor = _anchor("approve")
    rows = await db.scalars(
        select(WorkItem.work_item_id).where(
            WorkItem.work_item_id.in_(ids),
            WorkItem.project_id.in_(projects),
            _waiting("approve", now),
            _failures("approve", anchor) < MAX_FAILURES,
        )
    )
    return set(rows)


async def projects_awaiting_gate(
    db: AsyncSession, *, now: datetime | None = None, among: set[UUID] | None = None,
) -> set[UUID]:
    """승인 검사를 기다리는 승인분이 있는 프로젝트 id를 돌려준다. 내보내기가 시작을 미룰 때 쓴다.

    `among`을 주면 그 프로젝트들만 본다. 내보내기 이력 화면은 한 프로젝트만 조회한다.
    """
    projects = await enabled_project_ids(db)
    if among is not None:
        projects = [p for p in projects if p in among]
    if not projects:
        return set()
    now = now or datetime.now(UTC)
    anchor = _anchor("approve")
    rows = await db.scalars(
        select(WorkItem.project_id).distinct().where(
            WorkItem.project_id.in_(projects),
            _waiting("approve", now),
            _failures("approve", anchor) < MAX_FAILURES,
        )
    )
    return set(rows)


async def gate_wait_of(db: AsyncSession, project: Project) -> tuple[bool, bool]:
    """그 프로젝트의 내보내기 시작을 승인 검사 때문에 미룰 수 있는지와 지금 미루는지를 돌려준다.

    내보내기 이력 화면이 쓰며, 워커가 잡을 때 쓰는 조건(`projects_awaiting_gate`)을 그대로 쓴다.
    화면이 대기 시간만으로 처리 지연을 판정하면
    서버가 의도적으로 미루는 동안에도 멈춘 것으로 표시한다.

    Returns:
        `(품질검수 켬, 승인 검사를 기다리는 승인분 있음)`.
    """
    if not qc_settings.of_project(project.settings).enabled:
        return False, False
    waiting = await projects_awaiting_gate(db, among={project.project_id})
    return True, project.project_id in waiting


def lease(pairs: list[tuple[UUID, str]]) -> list[tuple[UUID, str]]:
    """아직 보내지 않은 건만 골라 `LEASE_SECONDS` 동안 다시 보내지 않게 표시한다.

    투입 주기가 실행 시간보다 짧아서 표시가 없으면 실행 중인 건을 주기마다 다시 보낸다.
    표시는 공유 Redis에 두고, Redis가 없는 로컬·테스트 환경과 Redis 오류에서는 모두 보낸다.
    두 번 실행돼도 쓰기 직전 확인(`recorded_by_other`)이 기록을 하나만 남긴다.
    """
    client = redis_infra.get_redis()
    if client is None:
        return list(pairs)
    out = []
    for wid, trigger in pairs:
        key = _LEASE_KEY.format(work_item_id=wid, trigger=trigger)
        try:
            if client.set(key, "1", nx=True, ex=LEASE_SECONDS):
                out.append((wid, trigger))
        except Exception:  # noqa: BLE001 — 표시 실패는 중복 실행일 뿐이고 기록은 하나로 남는다
            log.warning("캡션 QC 투입 표시 실패 work_item=%s trigger=%s", wid, trigger)
            out.append((wid, trigger))
    return out


def release(work_item_id: UUID, trigger: str) -> None:
    """끝난 건의 투입 표시를 지운다. 실행이 기록했거나 대상이 아니라고 끝났을 때 부른다.

    표시의 키는 작업과 시점뿐이라 제출본을 구분하지 않는다. 지우지 않으면 반려 뒤 바로 다시 제출한
    버전이 앞선 표시가 끝날 때까지(`LEASE_SECONDS`) 검사를 기다린다.
    끝난 건은 `find_owed`가 더 찾지 않으므로 지워도 다시 보내지 않는다.
    """
    client = redis_infra.get_redis()
    if client is None:
        return
    key = _LEASE_KEY.format(work_item_id=work_item_id, trigger=trigger)
    try:
        client.delete(key)
    except Exception:  # noqa: BLE001 — 지우지 못하면 `LEASE_SECONDS` 뒤에 다시 보낼 뿐이다
        log.warning("캡션 QC 투입 표시 해제 실패 work_item=%s trigger=%s", work_item_id, trigger)


def retry_after(work_item_id: UUID, trigger: str) -> None:
    """실패한 건을 `RETRY_SECONDS` 뒤에 다시 보낼 수 있게 표시 시간을 줄인다."""
    client = redis_infra.get_redis()
    if client is None:
        return
    key = _LEASE_KEY.format(work_item_id=work_item_id, trigger=trigger)
    try:
        client.set(key, "1", ex=RETRY_SECONDS)
    except Exception:  # noqa: BLE001 — 줄이지 못하면 `LEASE_SECONDS` 뒤에 다시 보낼 뿐이다
        log.warning("캡션 QC 재시도 표시 실패 work_item=%s trigger=%s", work_item_id, trigger)


def record_failure(db: AsyncSession, item: WorkItem, trigger: str, error: str) -> None:
    """실패한 검사 시도를 감사 이벤트로 남긴다. `error`는 원인(`model_failed`·예외 이름)이다."""
    audit.emit(
        db, event_type=FAILED_EVENT, target_kind="work_item",
        target_id=item.work_item_id, project_id=item.project_id,
        payload={"trigger": trigger, "error": error[:200]},
    )
