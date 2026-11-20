"""납품 요청과 처리. 승인분을 납품 패키지 zip으로 묶어 S3에 저장한다.

패키지 내용과 규칙은 `export_package.py`가 정한다. 이 모듈은 언제 만들고, 어디에 두고,
누가 받는지만 다룬다.

워커가 중단돼도 내보내기가 `만드는 중`에 계속 남지 않도록 한 건을 세 단계로 처리한다.
1. 잡기: `pending`을 `running`으로 바꾸고 시도 수를 1 올린 뒤 커밋한다.
2. 만들기: 그 행을 `FOR UPDATE`로 잠근 채 조립·업로드하고 결과를 커밋한다.
3. 회수: 다음 beat 시작 때 아무도 잠그지 않은 `running`을 되돌린다.
워커가 만드는 도중 중단되면 커넥션이 끊겨 행 잠금이 풀리고 `running` 상태만 남는다. 잠기지 않은
`running` 행은 만들던 워커가 없다는 신호라서 시각 필드 없이 가려낼 수 있다. 시도 수는 잡을 때
이미 올라 있다. 그래서 매번 워커를 중단시키는 건(메모리 초과 등)도 상한에서 `failed`로 멈춘다.
주의: 잡기와 만들기를 한 트랜잭션으로 합치면 워커가 중단될 때 시도 수가 오르지 않은 채
`pending`으로 돌아가, 그런 건이 끝없이 반복된다.

화면은 `attempts`·`max_attempts`와 요청 후 경과 시간을 보고 상태를 표시한다. 회수도 워커가
돌린다. 그래서 워커가 아예 없으면 이 모듈은 상태를 바꾸지 못하고, 화면이 경과 시간으로 지연을
알린다.
"""
import logging
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import (
    CompletedDetail,
    CompletedFilter,
    CompletedPage,
    CompletedReview,
    CompletedRevision,
    CompletedRow,
    CompletedSummary,
    ExportCaption,
    ExportDownloadResponse,
    ExportDTO,
)
from sqlalchemy import and_, case, column, exists, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import Clip, Export, Label, Project, Review, User, Video, WorkItem
from api_gateway.services import (
    audit,
    authz,
    caption,
    operating_time,
    platform_credentials,
    reject_reasons,
    text_search,
    video_meta,
)
from api_gateway.services.caption_qc import backlog as qc_backlog
from api_gateway.services.exports import export_package
from api_gateway.services.joblock import single_runner
from api_gateway.services.media import storage
from api_gateway.services.transient import is_transient_boto

log = logging.getLogger(__name__)

#: 워커가 한 내보내기를 잡을 수 있는 횟수(첫 시도 포함). 일시 오류로 되돌리거나 워커 유실로
#: 회수할 때마다 하나씩 쓴다. beat가 30초 주기라 이 횟수로 충분하다. 넘으면 실패로 확정해 사람이
#: 확인하게 한다.
_MAX_ATTEMPTS = 5

#: 내보내기 시작을 미루는 최대 시간. 품질검수를 켠 프로젝트에 검사 대기 승인분이 있을 때만 미룬다.
#: 승인은 검사 전에 커밋되므로 승인 직후 내보내면 그 승인분이 검사 전 `pending`으로 실린다.
#: 검사는 보통 수십 초면 끝나므로 그동안 기다린다. 모델 호출이 실패하면 워커가 세 번까지 다시
#: 시도하는 동안 `pending`이다. 그 시간(2 × `caption_qc.backlog.RETRY_SECONDS` + 3 × 실행 상한
#: 약 130초 ≈ 9분)을 덮도록 10분으로 둔다. 이 시간이 지나면 기다리지 않는다. 검사하지 않은 건은
#: 납품을 막지 않는다.
_QC_WAIT = timedelta(minutes=10)

#: 내보내기 처리의 단일 실행 락 키('RBEX'). 락 획득·해제 규약은 `services/joblock.py`에 있다.
#: 패키지 조립은 승인분 전체를 읽고 zip을 쓰는 일이라 여러 워커가 동시에 만들면 메모리와
#: 대역폭을 나눠 쓰다 모두 느려진다. 그래서 만드는 워커를 하나로 둔다.
_LOCK_KEY = 0x52424558


#: `error` 필드에 싣는 코드. 화면이 `errorText` 사전으로 표시하고, 원문 예외는 로그에만 남긴다.
UPLOAD_RETRYING = "export_upload_retrying"   # 스토리지 일시 오류. 다음 beat가 다시 잡는다
UPLOAD_FAILED = "export_upload_failed"       # 스토리지 오류가 영구이거나 상한까지 반복됐다
BUILD_FAILED = "export_build_failed"         # 패키지 조립 중 예외. 코드·데이터 쪽 문제다
INTEGRITY_FAILED = "export_integrity_failed"  # 색인과 파일이 어긋났다
WORKER_LOST = "export_worker_lost"           # 만들던 워커가 사라져 회수 단계가 되돌렸다
LEGACY_FAILED = "export_failed"              # 오류 코드가 없는 옛 행에 쓰는 일반 코드
_CODES = frozenset({UPLOAD_RETRYING, UPLOAD_FAILED, BUILD_FAILED, INTEGRITY_FAILED, WORKER_LOST})

#: 완료된 내보내기가 뺀 건수를 `error` 필드에 적는 형식. 스키마를 바꾸지 않고 수를 전달한다.
#: 화면은 문장을 파싱하지 않고 수를 받는다. 읽는 곳은 `_to_dto` 하나다. 옛 행의 문장
#: (`제외 3건 — package.zip의 report.json 참조`)도 함께 읽는다.
_EXCLUDED = re.compile(r"^(?:excluded:|제외 )(\d+)")
#: 같은 필드에 이어 적는 품질검수 보류 수(`excluded:N held:M`). 옛 행에는 없다.
_HELD = re.compile(r" held:(\d+)")


def export_filename(
    slug: str, created_at: datetime, sample_size: int | None, export_id: UUID,
) -> str:
    """내려받을 파일 이름(`{slug}-{YYYYMMDD-HHMM}-{ID 앞 8자리}[-sample{N}].zip`)을 만든다.

    전량본·샘플·재전달분을 다운로드 폴더에서 열어 보지 않고 이름만 보고 구별할 수 있게 한다.
    slug는 계약이 `[a-z0-9-]`로 제한하므로 파일 이름에 안전하다.
    시각이 분 단위라 같은 분에 만든 두 패키지는 시각만으로 이름이 같아진다. 내보내기 ID 앞
    8자리를 붙여 구별한다. 화면의 ID 표기(앞 8자리)와 같은 값이라 이력 행과 맞춰 볼 수 있다.

    시각은 운영 시간대 기준이며 `services/operating_time` 한 곳에서 읽는다.
    파일 이름의 날짜와 통계 화면의 날짜가 다른 시간대를 쓰면 같은 납품이 두 날짜로 불린다.
    """
    at = created_at.astimezone(operating_time.OPERATING_TZ).strftime("%Y%m%d-%H%M")
    tail = f"-sample{sample_size}" if sample_size else ""
    return f"{slug}-{at}-{str(export_id)[:8]}{tail}.zip"


def _error_code_of(e: Export) -> str | None:
    """`error` 필드를 화면에 보낼 코드로 바꾼다. 원문(옛 행의 예외 문장)은 화면에 보내지 않는다.

    완료된 행의 `error` 필드는 제외 수를 담으므로 코드가 없다(`_excluded_of`가 읽는다).
    """
    if e.status == "completed" or not e.error:
        return None
    if e.error in _CODES:
        return e.error
    # 오류 코드가 없는 옛 행. `[일시] …`는 스토리지 일시 오류를 뜻한다.
    if e.error.startswith("[일시]"):
        return UPLOAD_RETRYING if e.status == "pending" else UPLOAD_FAILED
    return LEGACY_FAILED


def _excluded_of(e: Export) -> int | None:
    """완료된 내보내기가 납품 관문에 걸려 뺀 건수를 돌려준다.

    완료 전이면 None, 뺀 것이 없으면 0이다.

    보류 수를 따로 적지 않은 옛 행은 이 수에 품질검수 보류분이 섞여 있다(`_held_of`가 None).
    """
    if e.status != "completed":
        return None
    hit = _EXCLUDED.match(e.error or "")
    return int(hit.group(1)) if hit else 0


def _held_of(e: Export) -> int | None:
    """완료된 내보내기가 품질검수 보류로 뺀 건수를 돌려준다.

    완료 전이거나 두 수를 따로 적지 않은 옛 행이면 None이다.
    """
    if e.status != "completed":
        return None
    if not e.error:
        return 0
    hit = _HELD.search(e.error)
    return int(hit.group(1)) if hit else None


def _to_dto(
    e: Export, slug: str | None = None, name: str | None = None, *,
    qc_on: bool = False, qc_waiting: bool = False,
) -> ExportDTO:
    """Export 모델을 ExportDTO로 바꾼다. `error` 필드를 코드와 제외 수로 나눈다.

    Args:
        slug: 프로젝트 slug. 주면 내려받을 파일 이름을 함께 싣는다.
        name: 이력 행에 표시할 만든 사람의 이름. 찾지 못하면 값을 만들지 않고 `None`으로 둔다.
        qc_on: 프로젝트가 캡션 품질검수를 켰으면 참.
        qc_waiting: 프로젝트에 승인 검사를 기다리는 승인분이 있으면 참(`backlog.gate_wait_of`).
            둘 다 참이고 대기 중인 행이 `_QC_WAIT` 안이면 워커가 그 행을 잡지 않는다(`_claim_next`).
            그 동안은 미루기가 끝나는 시각을 `qc_wait_until`로 싣는다.
    """
    deadline = (
        e.created_at + _QC_WAIT
        if qc_on and qc_waiting and e.status == "pending" and e.created_at else None
    )
    return ExportDTO(
        export_id=e.export_id,
        project_id=e.project_id,
        created_by=e.created_by,
        created_by_name=name,
        status=e.status,
        sample_size=e.sample_size,
        omit_optional=bool(e.omit_optional),
        manifest_s3_key=e.manifest_s3_key,
        item_count=e.item_count,
        error=_error_code_of(e),
        excluded_count=_excluded_of(e),
        qc_held_count=_held_of(e),
        attempts=e.attempts or 0,
        max_attempts=_MAX_ATTEMPTS,
        filename=export_filename(slug, e.created_at, e.sample_size, e.export_id) if slug else None,
        created_at=e.created_at,
        completed_at=e.completed_at,
        qc_wait_until=deadline if deadline and datetime.now(UTC) < deadline else None,
    )


async def create(db: AsyncSession, project_id: UUID, user: User) -> ExportDTO:
    """내보내기 작업을 `pending` 상태로 큐에 등록한다. 패키지 생성은 워커가 비동기로 한다.

    행만 바로 INSERT한다. 패키지는 workers/worker-io의 run_pending_exports celery 태스크가
    30초 주기로 만든다. 그래서 데이터가 커도 HTTP 응답 시간이 늘지 않고, 워커 부담이
    요청과 분리된다.

    test 환경(`ENVIRONMENT=test`)이나 동기 처리가 필요한 호출은 `process_pending(db)`을 바로
    불러 즉시 완료시킬 수 있다.

    새 내보내기는 언제나 전량이다. 샘플 건수(`sample_size`)와 선택 항목 제외(`omit_optional`)
    열은 그 값이 든 기존 행을 워커가 같은 형식으로 만들도록 남겨 둔다.

    Raises:
        HTTPException: 프로젝트가 없거나(404 project_not_found) 관리자가 아닌 경우
            (403 not_project_admin).
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)

    export = Export(project_id=project_id, created_by=user.user_id, status="pending")
    db.add(export)
    await db.flush()  # export_id를 감사 행의 target_id로 쓰려면 먼저 받아야 한다
    audit.emit(
        db, event_type="export.created", target_kind="export",
        target_id=export.export_id, actor_user_id=user.user_id, project_id=project_id,
        payload={"sample_size": None, "omit_optional": False},
    )
    await db.commit()
    await db.refresh(export)
    qc_on, qc_waiting = await qc_backlog.gate_wait_of(db, project)
    return _to_dto(export, project.slug, qc_on=qc_on, qc_waiting=qc_waiting)


async def process_pending(db: AsyncSession, limit: int = 1) -> int:
    """대기 중인 내보내기를 limit건까지 처리한다. beat 태스크가 30초 주기로 부른다.

    `single_runner`로 한 번에 하나만 실행한다. 다른 실행이 처리 중이면 아무 건도 잡지 않고
    0을 돌려준다. beat는 기본값으로 불러 호출마다 한 건만 만든다. 태스크 시간 제한은 한 호출에
    걸리므로 여러 건을 이어 만들면 뒤의 건이 제한에 걸려 중단되고 시도 수만 쓴다.

    먼저 회수 단계(`reclaim_stale`)를 돌리고, 한 건씩 잡아(`_claim_next`) 만든다(`_build`).
    단계마다 커밋하므로 한 건의 실패가 앞서 끝낸 건을 되돌리지 않는다.

    한 번의 호출에서 같은 건을 두 번 잡지 않는다. 일시 오류로 `pending`에 돌아간 건을 바로
    다시 잡으면, 30초 간격이어야 할 재시도가 한 호출 안에서 연달아 돌아 상한을 곧바로 소진한다.

    Returns:
        이번 호출에서 잡은 건수.
    """
    async with single_runner(_LOCK_KEY) as got:
        if not got:
            return 0
        await reclaim_stale(db)
        waiting = await qc_backlog.projects_awaiting_gate(db)
        seen: set[UUID] = set()
        for _ in range(limit):
            export_id = await _claim_next(db, seen, waiting)
            if export_id is None:
                break
            seen.add(export_id)
            await _build(db, export_id)
        return len(seen)


async def reclaim_stale(db: AsyncSession) -> int:
    """아무도 잠그지 않은 `running` 행을 되돌린다. 처리의 회수 단계다.

    만드는 워커는 그 행을 `FOR UPDATE`로 잠그고 있다(`_build`). `SKIP LOCKED`로 잡히는
    `running` 행은 잠근 워커가 없는 행이다. 워커가 도중에 중단됐거나(재배포·메모리 초과·태스크
    시간 제한) 트랜잭션이 실패한 경우다. 시도가 남았으면 `pending`으로 되돌려 다시 잡게 한다.
    다 썼으면 `failed`로 확정해 사람이 확인하게 한다. 사유 코드는 둘 다 `export_worker_lost`다.

    Returns:
        되돌리거나 확정한 건수.
    """
    stale = (await db.scalars(
        select(Export)
        .where(Export.status == "running")
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )).all()
    for e in stale:
        e.error = WORKER_LOST
        if e.attempts < _MAX_ATTEMPTS:
            e.status = "pending"
        else:
            e.status = "failed"
            e.completed_at = datetime.now(UTC)
        log.warning(
            "export 회수 — 만들던 워커가 없다 export=%s attempts=%s/%s → %s",
            e.export_id, e.attempts, _MAX_ATTEMPTS, e.status,
        )
    await db.commit()
    return len(stale)


async def _claim_next(
    db: AsyncSession, seen: set[UUID], waiting: set[UUID] | None = None
) -> UUID | None:
    """가장 오래 기다린 `pending` 하나를 잡는다. `running`으로 바꾸고 시도 수를 올린 뒤 커밋한다.

    커밋해야 다른 워커와 화면이 잡힌 상태를 본다. 시도 수를 잡을 때 올리는 이유는 모듈
    docstring에 있다(워커를 중단시키는 건이 끝없이 돌지 않게 한다). 단계 사이에 ORM 객체를
    넘기면 롤백 뒤 만료된 속성에 접근해 실패하므로 id 값만 돌려준다.

    `waiting`은 승인 검사를 기다리는 프로젝트다(`caption_qc.backlog.projects_awaiting_gate`).
    그 프로젝트의 내보내기는 요청 후 `_QC_WAIT`이 지날 때까지 잡지 않는다. 행을 빼지 않고
    시작만 미루므로 납품 게이트(`approved_rows_stmt`)와 보고서의 분모는 그대로다.
    """
    stmt = (
        select(Export)
        .where(Export.status == "pending")
        .order_by(Export.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if seen:
        stmt = stmt.where(Export.export_id.not_in(seen))
    if waiting:
        stmt = stmt.where(or_(
            Export.project_id.not_in(waiting),
            Export.created_at < datetime.now(UTC) - _QC_WAIT,
        ))
    export = await db.scalar(stmt)
    if export is None:
        await db.rollback()
        return None
    export.status = "running"
    export.attempts = (export.attempts or 0) + 1
    export_id = export.export_id
    await db.commit()
    return export_id


async def _build(db: AsyncSession, export_id: UUID) -> None:
    """잡은 한 건을 잠근 채로 만든다. 끝나면 결과를 커밋해 잠금을 푼다.

    다시 읽었을 때 `running`이 아니면(회수 단계가 먼저 되돌린 경우) 그대로 둔다.
    조립 밖에서 올라온 예외(DB 오류 등)는 이 건만 롤백하고 넘어간다. 행은 `running`으로 남고,
    다음 회수 단계가 시도 수를 유지한 채 되돌린다.

    트랜잭션은 REPEATABLE READ로 연다. 조립은 후보 행(`approved_rows_stmt`)과 보류 목록
    (`qc_held_ids`), 승인 시각·수정 횟수를 따로 읽는다. READ COMMITTED에서는 읽기마다 시점이
    달라서, 그 사이 보류로 바뀐 건이 패키지에 담기면서 제외 목록에도 실린다.
    한 시점의 스냅숏에서 읽으면 모든 읽기가 같은 상태를 본다. 이 트랜잭션은 작업 행을 쓰지 않으므로
    직렬화 실패가 나지 않는다.
    """
    await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    export = await db.scalar(
        select(Export)
        .where(Export.export_id == export_id, Export.status == "running")
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if export is None:
        await db.rollback()
        return
    try:
        await _run_single(db, export)
        await db.commit()
    except Exception:  # noqa: BLE001 한 건의 실패로 배치 전체가 멈추지 않게 한다
        await db.rollback()
        log.exception("export 처리 중 예외 — 회수 절이 되돌린다 export=%s", export_id)


async def _qc_checked_by_map(db: AsyncSession, ids: list[UUID]) -> dict[UUID, str]:
    """work_item별로 승인 직후 검사를 한 주체(`model` · `rules`)를 돌려준다.

    검사 기록이 없는 건은 키가 없다.

    승인 실행과 다시 검사 실행만 본다(`CAPTION_QC_GATE_TRIGGERS`).
    제출 실행은 리뷰어에게 보여 주는 근거다. 납품 여부는 승인 실행이 정한다. 리뷰어가 승인하면서
    텍스트를 고쳤을 수 있으므로 제출 때의 모델 검사는 승인본 검사로 세지 않는다.

    `adapter_key`(`rules`·`llm`·`bedrock`·`dummy`)는 두 값으로 줄인다. 화면에 필요한 것은 모델
    검사 여부 하나다. 어댑터 이름을 그대로 보내면 화면이 어댑터 목록을 알아야 하고, 어댑터가
    늘 때마다 화면을 고쳐야 한다.
    """
    if not ids:
        return {}
    from api_gateway.models import CaptionQcRun
    from api_gateway.models.work import CAPTION_QC_GATE_TRIGGERS

    # 건마다 마지막 승인·다시 검사 실행 하나를 고른다. 승인은 여러 번 날 수 있다(반려 후 재승인).
    # 다시 검사로 보류가 풀린 건은 그 실행이 납품을 정했으므로 옛 승인 실행보다 앞선다.
    latest = (
        select(
            CaptionQcRun.work_item_id,
            func.max(CaptionQcRun.created_at).label("at"),
        )
        .where(
            CaptionQcRun.work_item_id.in_(ids),
            CaptionQcRun.trigger.in_(CAPTION_QC_GATE_TRIGGERS),
        )
        .group_by(CaptionQcRun.work_item_id)
        .subquery()
    )
    # 주의: 다시 조인할 때도 `trigger` 조건을 건다. `created_at`의 기본값이 `now()`라서 한
    # 트랜잭션에서 쓴 제출 실행과 승인 실행의 시각이 같다. 시각만으로 조인하면 제출 실행도
    # 함께 잡힌다. 그러면 모델이 본 건이 `규칙만`으로 읽혀 완료 목록의 `규칙만 검사` 필터와
    # 이 필드가 달라진다.
    stmt = select(CaptionQcRun.work_item_id, CaptionQcRun.adapter_key).join(
        latest,
        (CaptionQcRun.work_item_id == latest.c.work_item_id)
        & (CaptionQcRun.created_at == latest.c.at)
        & (CaptionQcRun.trigger.in_(CAPTION_QC_GATE_TRIGGERS)),
    )
    return {
        wid: ("rules" if key == "rules" else "model")
        for wid, key in (await db.execute(stmt)).all()
    }


def qc_rules_only_ids():
    """승인 직후 검사를 규칙만으로 한 work_item의 id를 고르는 문을 만든다.

    "모델이 보지 못함"의 판정 기준이다.

    이 정의를 쓰는 곳은 수를 세는 곳(`qc_unreviewed_total`, 내보내기 버튼 옆 경고)과
    목록을 거르는 곳(완료 목록 `filter=qc_rules`) 둘이다. 각자 구현하면 한쪽만 `trigger`를 보거나
    한쪽만 `adapter_key`를 비교할 수 있다. 그러면 경고는 3건인데 목록은 5행인 식으로 수와
    목록이 달라진다.

    `null`(검사가 돌지 않은 건)은 검사가 없던 때의 승인분이라 포함하지 않는다. 합치면 QC를
    켠 직후 옛 승인분 수천 건이 모두 경고가 된다.

    Returns:
        `select(work_item_id)`. `IN`이나 `join`에 그대로 넣는 서브쿼리 문이다.
    """
    from api_gateway.models import CaptionQcRun
    from api_gateway.models.work import CAPTION_QC_GATE_TRIGGERS

    # 건마다 마지막 승인·다시 검사 실행 하나를 고른다. 승인은 여러 번 날 수 있다(반려 후 재승인).
    # 다시 검사로 보류가 풀린 건은 그 실행이 납품을 정했으므로 옛 승인 실행보다 앞선다.
    latest = (
        select(
            CaptionQcRun.work_item_id,
            func.max(CaptionQcRun.created_at).label("at"),
        )
        .where(CaptionQcRun.trigger.in_(CAPTION_QC_GATE_TRIGGERS))
        .group_by(CaptionQcRun.work_item_id)
        .subquery()
    )
    # 주의: 다시 조인할 때도 `trigger` 조건을 건다. `created_at`의 기본값이 `now()`라서 한
    # 트랜잭션에서 쓴 제출 실행과 승인 실행의 시각이 같다. 시각만으로 조인하면 제출 실행의
    # `adapter_key`가 섞여 모델이 본 건이 `규칙만`으로 잡힌다.
    return (
        select(latest.c.work_item_id)
        .join(
            CaptionQcRun,
            (CaptionQcRun.work_item_id == latest.c.work_item_id)
            & (CaptionQcRun.created_at == latest.c.at)
            & (CaptionQcRun.trigger.in_(CAPTION_QC_GATE_TRIGGERS)),
        )
        .where(CaptionQcRun.adapter_key == "rules")
    )


async def qc_unreviewed_total(db: AsyncSession, project_id: UUID) -> int:
    """승인분 중 모델 검사를 받지 못한 건의 전수를 센다. 납품 버튼 옆에 표시되는 수다.

    페이지 단위로 세지 않는다. 100건만 실린 목록으로 전수를 표시하면 사실과 달라진다.

    `_deliverable_scan`과 달리 상한이 없다. `_deliverable_scan`은 행마다 pydantic으로 판정하므로
    전수를 세려면 payload를 모두 적재해야 한다. 여기서 보는 것은 일반 열 두 개(`trigger`·
    `adapter_key`)다. 그래서 `COUNT` 하나로 물량과 무관하게 정확히 센다.

    무엇이 "규칙만"인지는 `qc_rules_only_ids`가 정한다. 완료 목록의 같은 필터도 그 문을 그대로
    쓰므로 이 수와 목록은 항상 같다.
    """
    approved = approved_rows_stmt(project_id).order_by(None).subquery()
    stmt = (
        select(func.count())
        .select_from(approved)
        .where(approved.c.work_item_id.in_(qc_rules_only_ids()))
    )
    return await db.scalar(stmt) or 0


async def _approved_at_map(db: AsyncSession, ids: list[UUID]) -> dict[UUID, object]:
    """work_item별 마지막 승인 판정 시각을 돌려준다. 승인 판정이 없는 건은 키가 없다.

    승인 시각이 없어도 납품을 막지 않는다(`export_package.check_row`).
    """
    if not ids:
        return {}
    stmt = (
        select(Review.work_item_id, func.max(Review.reviewed_at))
        .where(
            Review.work_item_id.in_(ids),
            Review.verdict.in_(("approve", "modify_and_approve")),
        )
        .group_by(Review.work_item_id)
    )
    return {wid: at for wid, at in (await db.execute(stmt)).all()}


async def _revision_map(db: AsyncSession, ids: list[UUID]) -> dict[UUID, int]:
    """work_item별 검수 수정 횟수(반려와 수정 후 승인 판정의 수)를 돌려준다. 없는 건은 키가 없다.

    작업의 `rejection_count`는 다른 사람이 큐에서 받으면 0으로 돌아가므로 판정 기록을 센다.
    """
    if not ids:
        return {}
    stmt = (
        select(Review.work_item_id, func.count())
        .where(
            Review.work_item_id.in_(ids),
            Review.verdict.in_(("reject", "modify_and_approve")),
        )
        .group_by(Review.work_item_id)
    )
    return {wid: int(n) for wid, n in (await db.execute(stmt)).all()}


def _spread(rows: list, n: int) -> list:
    """전체에서 고르게 n건을 뽑는다. 앞에서부터 n건을 자르지 않는다.

    정렬이 제출 순이라 앞에서부터 자르면 샘플이 한 영상·한 프로그램에 몰린다.
    그러면 샘플이 데이터의 일부 구간만 담아 형식을 대표하지 못한다.

    난수를 쓰지 않는다. 같은 입력에서 같은 샘플이 나와야 같은 샘플을 다시 요청받았을 때
    그대로 줄 수 있다.
    """
    if n <= 0 or len(rows) <= n:
        return rows
    stride = len(rows) / n
    return [rows[int(i * stride)] for i in range(n)]


def _excluded_note(report: dict, held: int) -> str | None:
    """성공한 내보내기에도 제외 건수를 적는다. 화면이 그 수를 보고 사유를 확인하러 가게 한다.

    스키마를 바꿔 새 열을 늘리는 대신 기존 `error` 필드를 쓴다. 실패 여부는 `status`로 구분한다.
    값은 기계용 표기(`excluded:N held:M`)이고 읽는 곳은 `_excluded_of`·`_held_of`다.
    문장을 적으면 화면이 그 문장을 그대로 표시해 사용자에게 zip을 풀어 보라고 안내한다.

    `report.json`의 `excluded`는 관문 제외와 품질검수 보류를 합친 수다. 화면의 `납품 불가`는
    관문 제외만 세므로(완료 목록의 같은 축) 둘을 나눠 적는다. 합치면 내보내기 창은 `납품 불가 0`,
    이력 행은 `납품 불가 2`처럼 같은 이름으로 다른 수를 표시한다.
    """
    total = report.get("excluded") or 0
    if not total:
        return None
    return f"excluded:{total - held} held:{held}"


# 납품 가능 건수를 전수로 세는 상한. 정확히 셀 수 있는 범위의 경계다(성능 튜닝 값과 다르다).
# 넘으면 세지 않고 화면이 "세지 않음"을 표시한다. 2,000은 한 프로젝트를 한 번 훑어도
# 지연이 눈에 띄지 않는 수준이다. 승인 시점에 판정을 열에 저장하면 이 상한을 없앨 수 있다.
DELIVERABLE_SCAN_LIMIT = 2_000


def approved_rows_stmt(project_id: UUID):
    """내보내기에 실릴 행을 고르는 쿼리를 만든다. 완료 탭도 이 쿼리를 쓴다.

    "무엇이 완료인가"의 정의를 하나로 둔다. 화면이 따로 세면 화면에 보인 것과 실제로 나가는
    것이 달라진다. 그 차이는 내보내기를 받아 보기 전에는 드러나지 않는다.

    Clip은 outer join이다. 클립 없는 work_item(영상 단위 작업)도 승인되면 나간다.
    inner join이면 그 행들이 알림 없이 빠지고, 빠진 행은 받는 쪽에서 셀 수 없다.

    `deleted_at`은 의도적으로 거르지 않는다. 삭제된 영상의 승인분도 나간다.
    - 삭제는 오브젝트를 지우지 않는다. `media.video.soft_delete`는 `deleted_at`만 기록하고
      원본·재생본·분석본은 버킷에 그대로 남는다. 패키지는 미디어를 담지 않고
      `source_video_id`와 원본 기준 시각으로 가리키므로, 가리키는 대상이 남아 있다.
    - 걸러 내면 사람이 만든 라벨링 결과가 목록 정리 한 번으로 납품에서 사라진다.
    주의: 여기에 `Video.deleted_at.is_(None)`을 추가하지 않는다. 고치는 것처럼 보이지만
    승인된 산출물을 알림 없이 빼는 변경이다.

    캡션 품질검수는 보류만 뺀다. `qc_status`가 `held`인 것만 제외한다.
    `pending`(아직 돌지 않음·호출 실패)은 나간다. `pending`까지 빼면 검사 엔진이 멈춘 날 납품이
    0건이 된다.
    주의: `report.json`의 `excluded`는 후보 중 관문에 걸린 건만 세므로 여기서 빠진 보류분은
    잡히지 않는다. 그래서 `_run_single`이 `qc_held_ids`로 따로 세어 리포트에 싣는다.

    정렬은 전순서(total order)다. `submitted_at`은 NULL일 수 있다(수동 전이·옛 데이터).
    `ORDER BY submitted_at` 하나로는 값이 같은 행의 순서가 쿼리마다 달라진다. 그러면 `더 보기`로
    받은 2페이지에 1페이지 행이 다시 오거나 어떤 행은 끝내 오지 않는다.
    그래서 `work_item_id`를 동점 처리 키로 붙인다. 어떤 행이 나가는지는 그대로이고, 값이 같은
    행들 사이의 순서만 고정된다. 검수용 샘플(`_spread`)도 같은 입력에서 같은 결과가 나온다.
    """
    return (
        select(WorkItem, Video, Clip)
        .join(Video, WorkItem.video_id == Video.video_id)
        .outerjoin(Clip, WorkItem.clip_id == Clip.clip_id)
        .where(
            WorkItem.project_id == project_id,
            WorkItem.status == "approved",
            # 캡션 품질검수가 보류한 것만 뺀다.
            # `== 'passed'`로 바꾸지 않는다. 기존 승인분과 아직 검사하지 않은 건이 모두
            # `pending`이라 납품이 통째로 빈다. 엔진이 멈춘 날에도 0건이 나간다.
            # 여기서 빠진 건은 `qc_held_ids`가 따로 세어 `report.json`에 싣는다
            # (docstring의 주의 참고).
            WorkItem.qc_status != "held",
        )
        .order_by(WorkItem.submitted_at, WorkItem.work_item_id)
    )


def _fail(export: Export, code: str) -> None:
    """실패로 확정한다. 사유는 코드만 남기고, 원문은 부른 쪽이 로그에 남긴다."""
    export.status = "failed"
    export.error = code
    export.completed_at = datetime.now(UTC)


async def _run_single(db: AsyncSession, export: Export) -> None:
    """잡힌(`running`) 1건을 완료·실패·재시도 대기 중 하나로 전이한다. 커밋은 `_build`가 한다.

    실패 사유는 코드로 남기고 원문은 로그에 남긴다. 원문을 남기면 boto3 영문 원문이 이력 행에
    그대로 표시된다.
    조립 실패와 업로드 실패는 대응이 달라 나눈다. 업로드의 일시 오류는 기다리면 해결되지만,
    조립 실패는 다시 시도해도 결과가 같다.
    """
    rows = (await db.execute(approved_rows_stmt(export.project_id))).all()
    total_rows = len(rows)
    if export.sample_size:
        rows = _spread(rows, export.sample_size)
    # 품질검수 보류분. 후보에 없어 `rows`에 들어오지 않는 승인분이며 리포트에만 실린다.
    # 전량 패키지에만 싣는다. 샘플의 집계는 담긴 표본 기준이다(`included + excluded`가 표본
    # 크기다). 프로젝트 전수인 보류 건수를 섞으면 한 수 안에 분모가 둘이 된다.
    # 샘플 README는 전량 패키지의 리포트를 가리킨다.
    held = [] if export.sample_size else await qc_held_ids(db, export.project_id)
    approved_at = await _approved_at_map(db, [w.work_item_id for w, _v, _c in rows])
    revisions = await _revision_map(db, [w.work_item_id for w, _v, _c in rows])
    project = await db.get(Project, export.project_id)
    # 프로젝트가 고른 저장소 연결이 이 워커에 아직 로드되지 않았으면 올리기 전에 다시 읽는다.
    await platform_credentials.ensure_storage_loaded(
        db, platform_credentials.storage_target_of(project.settings if project else None))
    # 납품 패키지 구성은 `export_package` 모듈 docstring에 있다.
    # 미디어는 담지 않으므로 수십 MB에 그치고 브라우저로 받을 수 있다.
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "package.zip"
        try:
            report = export_package.write_package(
                list(rows), approved_at, out,
                project_name=(project.name if project else "dataset"),
                sampled_from=total_rows if export.sample_size else None,
                # 같은 프로젝트를 두 번 내보내도 카드로 어느 전달분인지 구별할 수 있게 한다.
                generated_at=export.created_at,
                qc_held=held,
                revisions=revisions,
                omit_optional=bool(export.omit_optional),
            )
        except export_package.PackageIntegrityError:
            log.exception("export 무결성 검사 실패 export=%s", export.export_id)
            _fail(export, INTEGRITY_FAILED)
            return
        except Exception:  # noqa: BLE001 — 조립은 코드·데이터 쪽 실패라 재시도로 안 낫는다
            log.exception("export 패키지 조립 실패 export=%s", export.export_id)
            _fail(export, BUILD_FAILED)
            return
        key = f"projects/{export.project_id}/exports/{export.export_id}/package.zip"
        try:
            # 프로젝트가 고른 저장소에 올리고, 내려받기가 같은 버킷으로 서명하도록 버킷을 기록한다.
            bucket = storage.upload_file(
                out, key, content_type="application/zip",
                store=storage.for_project(project.settings if project else None))
        except Exception as e:  # noqa: BLE001 — boto3/네트워크 등 광범위
            # 시도 수는 잡을 때 이미 셌다(`_claim_next`). 여기서 또 세면 한 번에 둘씩 줄어든다.
            if is_transient_boto(e) and export.attempts < _MAX_ATTEMPTS:
                # 다음 beat가 다시 잡도록 pending으로 되돌린다. 스로틀 한 번으로 실패시키지
                # 않고, 끝없이 재시도하지도 않는다. 상한을 넘으면 사람이 확인하도록 failed로
                # 확정한다.
                export.status = "pending"
                export.error = UPLOAD_RETRYING
                log.warning(
                    "export 업로드 일시 오류 — 재시도 대기 export=%s attempts=%s/%s err=%s",
                    export.export_id, export.attempts, _MAX_ATTEMPTS, e,
                )
                return
            log.error(
                "export 업로드 실패 export=%s attempts=%s/%s err=%s",
                export.export_id, export.attempts, _MAX_ATTEMPTS, e,
            )
            _fail(export, UPLOAD_FAILED)
            return
    export.manifest_s3_key = key
    export.storage_bucket = bucket or None
    # 담긴 것만 센다. 승인 건수를 실으면 화면의 건수와 파일에 든 건수가 달라진다.
    export.item_count = report["included"]
    export.error = _excluded_note(report, len(held))
    export.status = "completed"
    export.completed_at = datetime.now(UTC)


async def list_for_project(
    db: AsyncSession, project_id: UUID, user: User
) -> list[ExportDTO]:
    """프로젝트의 내보내기 목록을 최신순으로 돌려준다.

    Raises:
        HTTPException: 프로젝트가 없거나(404 project_not_found) 관리자가 아닌 경우
            (403 not_project_admin).
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)
    rows = list(await db.scalars(
        select(Export).where(Export.project_id == project_id).order_by(Export.created_at.desc())
    ))
    # 만든 사람의 이름을 한 번에 모아 조회한다. 행마다 조회하면 N+1 쿼리가 된다.
    # 보통 같은 사람이 여러 번 만들므로 조회할 사람 수는 이력 수보다 훨씬 적다.
    people = {e.created_by for e in rows}
    names = {
        uid: name for uid, name in (await db.execute(
            select(User.user_id, User.display_name).where(User.user_id.in_(people))
        )).all()
    } if people else {}
    # 대기 중인 행이 있을 때만 품질검수 대기를 조회한다. 끝난 이력에는 이 값이 필요 없다.
    qc_on, qc_waiting = (
        await qc_backlog.gate_wait_of(db, project) if any(e.status == "pending" for e in rows)
        else (False, False)
    )
    return [
        _to_dto(e, project.slug, names.get(e.created_by), qc_on=qc_on, qc_waiting=qc_waiting)
        for e in rows
    ]


async def download(db: AsyncSession, export_id: UUID, user: User) -> ExportDownloadResponse:
    """완료된 내보내기 패키지(`manifest_s3_key`)의 S3 presigned URL을 돌려준다.

    Raises:
        HTTPException: 내보내기가 없거나(404 export_not_found), 관리자가 아니거나
            (403 not_project_admin), 완료되지 않은 경우(409 export_status_*).
    """
    export = await db.get(Export, export_id)
    if not export:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "export_not_found")
    await authz.require_project_admin(db, export.project_id, user)
    if export.status != "completed" or not export.manifest_s3_key:
        raise HTTPException(status.HTTP_409_CONFLICT, f"export_status_{export.status}")
    if not export.storage_bucket:
        # 패키지를 올린 버킷 기록이 없으면 어느 연결로 서명할지 알 수 없다.
        raise platform_credentials.CredentialsMissingError(
            platform_credentials.STORAGE_NOT_CONFIGURED)
    # 라벨링 결과물은 보호 대상이라 반출을 감사 로그에 남긴다.
    # presigned URL을 발급하는 시점에 남긴다. S3에서 실제로 바이트를 받아 간 시점은 이 서버가
    # 알 수 없다. 그래서 접근 권한과 같은 URL을 받은 사람을 기록한다.
    audit.emit(
        db, event_type="export.downloaded", target_kind="export",
        target_id=export.export_id, actor_user_id=user.user_id,
        project_id=export.project_id,
        payload={"item_count": export.item_count, "sample_size": export.sample_size},
    )
    await db.commit()
    project = await db.get(Project, export.project_id)
    return ExportDownloadResponse(
        # 받을 파일 이름을 서명에 넣는다. 넣지 않으면 모든 파일이 `package.zip`으로 받아진다.
        download_url=storage.presign_get(
            export.manifest_s3_key,
            bucket=export.storage_bucket,
            filename=export_filename(
                project.slug, export.created_at, export.sample_size, export.export_id,
            )
            if project else None,
        ),
        expires_in=settings.s3_presign_ttl_seconds,
        item_count=export.item_count,
    )


def _caption_of(payload: dict) -> ExportCaption:
    """payload의 캡션을 계약 모양(`ExportCaption`)으로 바꾼다. payload 모양이 달라도 읽는다.

    `caption`이 문장 개체가 아닌 문자열인 옛 payload가 남아 있다. dict로 가정하면
    `AttributeError`가 난다. 이 예외 하나가 프로젝트 전체 처리를 멈춘다.

    `brief`는 기준 함수(`caption.final_text`)로 채운다. payload의 `brief` 키를 그대로
    읽으면 제출을 통과한 문장 배열 payload가 완료 목록에서 "캡션 없음"으로 표시된다.
    문장 수와 구간은 배열에서 세므로 문장 배열은 그대로 싣는다.
    """
    cap = payload.get("caption")
    if isinstance(cap, str):
        return ExportCaption(brief=cap)   # 문장 개체가 없는 옛 payload의 모양
    if not isinstance(cap, dict):
        return ExportCaption()
    return ExportCaption(
        brief=caption.final_text(payload),
        brief_sentences=_list_of(cap.get("brief_sentences")),
    )


def _split_sentences(text: str) -> list[str]:
    """문자열 캡션을 화면과 같은 규칙으로 문장으로 나눈다.

    규칙은 `caption.split_sentences` 한 곳에 둔다. 화면은 같은 캡션을 나눠 그리고 이 함수는
    문장 수를 센다. 규칙이 다르면 보이는 문장 수와 배지가 어긋난다.

    문자열 캡션에만 쓴다. 문장 배열(`brief_sentences`)이 있는 payload는 배열을 기준으로 쓴다.
    """
    return caption.split_sentences(text)


def _list_of(value) -> list:
    """dict 원소만 남긴 list를 돌려준다. 계약이 `list[dict]`라 다른 원소는 pydantic이 막는다."""
    if not isinstance(value, list):
        return []
    return [x for x in value if isinstance(x, dict)]



# 완료 목록.


def _search_filter(like: str):
    """완료 목록의 검색 조건을 만든다. 캡션·영상 제목·프로그램명을 본다.

    `like`는 `text_search.contains`가 만든 패턴이다. `%`·`_`를 글자 그대로 찾는다.

    서버에서 전수로 찾는다. 페이지에 실린 행만 찾으면 "없음"이 사실과 달라진다.
    완료본은 수백~수만 건이다. 화면에 실린 50건 안에서 찾으면 대부분 결과가 없고,
    그 결과가 실제로 없는 것으로 읽힌다.

    캡션은 세 모양을 모두 본다. 읽는 규칙은 `caption.sentences_of`·`final_text`와 같다.

    1. `payload -> caption -> brief_sentences[*].text`: 지금 화면이 보내는 모양.
       삭제 표시한 문장은 최종 캡션에 없으므로 찾지 않는다(`caption.live_sentences`).
    2. `payload -> caption -> brief`: 문장 배열이 없는 payload.
    3. `payload -> caption`이 문자열: 가장 오래된 모양.

    한 모양만 보면 다른 모양으로 저장된 캡션이 검색 결과에서 알림 없이 빠진다.
    최종 캡션 밖의 글자는 보지 않는다. 화면은 최종 캡션만 보여 주므로, 그 밖의 글자로 걸린
    행에서는 검색어를 찾을 수 없다.
    """
    esc = text_search.ESCAPE
    cap = WorkItem.payload["caption"]
    brief = cap["brief"].astext
    legacy = cap.astext      # 문자열 캡션
    # 문장 배열이 아니면 빈 배열로 바꿔 펼친다.
    # 문자열 캡션에 `jsonb_array_elements`를 부르면 오류가 난다.
    arr = cap["brief_sentences"]
    sents = func.jsonb_array_elements(
        case((func.jsonb_typeof(arr) == "array", arr), else_=literal_column("'[]'::jsonb"))
    ).table_valued(column("value", JSONB)).alias("sent")
    in_sentences = exists(
        select(1).select_from(sents).where(and_(
            sents.c.value["text"].astext.ilike(like, escape=esc),
            func.coalesce(sents.c.value["deleted"].astext, "false") != "true",
        ))
    )
    # 2와 3은 그 모양일 때만 본다. `caption`이 개체일 때 `->>`는 개체 전체의 JSON 글자를
    # 돌려주므로 삭제 표시한 문장이나 키 이름까지 검색에 걸린다.
    # 문장 배열이 있으면 `brief`는 파생값이라 보지 않는다.
    return or_(
        in_sentences,
        and_(func.coalesce(func.jsonb_typeof(arr), "") != "array", brief.ilike(like, escape=esc)),
        and_(func.jsonb_typeof(cap) == "string", legacy.ilike(like, escape=esc)),
        Video.title.ilike(like, escape=esc),
        Video.program_name.ilike(like, escape=esc),
    )


async def _gate_filtered_page(
    db: AsyncSession, base, *, want_deliverable: bool, limit: int, offset: int
) -> tuple[list, int, int, int]:
    """납품 관문으로 거른 한 페이지를 돌려준다. 판정은 `check_row` 한 곳에서 한다.

    규칙을 SQL로 옮기면 상한 없이 거를 수 있지만 같은 판정이 두 곳에 생긴다. 두 판정이
    어긋나도 내보내기를 받아 보기 전에는 드러나지 않는다. 그래서 패키지가 쓰는 함수를 그대로
    실행한다.

    그 대가로 상한이 있다. 관문이 행마다 pydantic으로 검증하므로 거르려면 행을 payload째
    적재해야 한다. 그래서 `DELIVERABLE_SCAN_LIMIT`까지만 본다(`_deliverable_scan`과 같은 상한).
    화면은 돌려준 수로 잘렸는지 판단한다. 수를 숨기면 `없음` 표시가 사실과 달라진다.

    Args:
        base: 후보 문(`approved_rows_stmt` + 검색 조건). 정렬이 있어야 한다.
            페이지를 파이썬에서 자르므로 정렬이 없으면 페이지마다 순서가 달라진다.
        want_deliverable: 관문을 통과한 행을 고르려면 True, 막힌 행을 고르려면 False.

    Returns:
        `(이 페이지의 행, 거른 뒤 전수, 관문을 돌린 행 수, 거르기 전 후보 전수)`.
    """
    candidates = await db.scalar(
        select(func.count()).select_from(base.order_by(None).subquery())
    ) or 0
    scanned = (await db.execute(base.limit(DELIVERABLE_SCAN_LIMIT))).all()
    approved_at = await _approved_at_map(db, [w.work_item_id for w, _v, _c in scanned])
    matched = [
        (w, v, c)
        for w, v, c in scanned
        if (not export_package.check_row(w, v, c, approved_at.get(w.work_item_id))[1])
        is want_deliverable
    ]
    return matched[offset : offset + limit], len(matched), len(scanned), candidates


async def list_completed(
    db: AsyncSession,
    project_id: UUID,
    user: User,
    *,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    summary: bool = True,
    completed_filter: CompletedFilter = "all",
) -> CompletedPage:
    """승인되어 내보내기에 실릴 작업 목록을 돌려준다. 완료 탭이 읽는다.

    관리자와 검수자가 볼 수 있다.
    완료본은 납품할 산출물이다. 이 권한은 열람용이고 편집 기능은 없다.
    라벨러에게 열면 다른 사람이 쓰고 승인된 캡션 전체가 보인다. 그래서 상세(`get_completed`)와
    같은 기준으로 막는다.

    행 집합은 내보내기와 같은 쿼리(`approved_rows_stmt`)로 고른다. 화면이 따로 세면 화면과
    실제로 나가는 것이 달라진다.

    `summary=False`면 전수 집계(납품 대상·보류·규칙만 검사)를 세지 않는다. 화면은 그 값을
    `completed_summary`에서 한 번 받는다. 그러지 않으면 `더 보기`를 누르거나 검색어를 한 글자
    칠 때마다 최대 2,000행 검증과 집계 세 개가 다시 돈다.

    필터링은 서버에서 한다. 화면이 거르면 실린 50건 안에서만 걸러져 `제외 없음`이 사실과
    달라진다. 세 필터 모두 판정을 새로 구현하지 않는다.
    - `qc_rules`: `qc_rules_only_ids()`를 쓴다. 버튼 옆 경고를 세는 것과 같은 문이라 수와
      목록이 항상 같다. SQL이라 물량과 무관하게 정확하다.
    - `deliverable`·`excluded`: 패키지가 쓰는 관문 `check_row`를 쓴다. 대신 상한이 있다
      (`_gate_filtered_page`).

    Raises:
        HTTPException: 프로젝트가 없거나(404 project_not_found) 볼 수 없는 역할인 경우
            (403 not_work_result_viewer).
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_work_result_viewer(db, project_id, user)

    base = approved_rows_stmt(project_id)
    like = text_search.contains(q)
    if like:
        base = base.where(_search_filter(like))
    # 규칙만 검사 필터는 SQL로 거른다. 상한이 없으므로 `total`이 그 필터의 전수다.
    if completed_filter == "qc_rules":
        base = base.where(WorkItem.work_item_id.in_(qc_rules_only_ids()))

    filter_scanned: int | None = None
    filter_candidates: int | None = None
    if completed_filter in ("deliverable", "excluded"):
        rows, total, filter_scanned, filter_candidates = await _gate_filtered_page(
            db, base,
            want_deliverable=completed_filter == "deliverable",
            limit=limit, offset=offset,
        )
    else:
        # 전체 수는 따로 센다. 실린 것만 세면 페이지를 넘길 때마다 "총 몇 건"이 달라진다.
        # 검색 중이면 이 수는 검색 결과 전체의 수이고, 화면도 그렇게 표시한다.
        total = await db.scalar(
            select(func.count()).select_from(base.order_by(None).subquery())
        ) or 0
        rows = (await db.execute(base.limit(limit).offset(offset))).all()

    # 승인 시각은 마지막 승인 판정의 시각이다. 없을 수도 있다(옛 데이터·수동 전이).
    ids = [w.work_item_id for w, _v, _c in rows]
    approved: dict[UUID, object] = {}
    if ids:
        stmt = (
            select(Review.work_item_id, func.max(Review.reviewed_at))
            .where(
                Review.work_item_id.in_(ids),
                Review.verdict.in_(("approve", "modify_and_approve")),
            )
            .group_by(Review.work_item_id)
        )
        approved = {wid: at for wid, at in (await db.execute(stmt)).all()}

    # 승인 직후 검사를 한 주체와 검사 중인 건은 실린 행만 조회한다. 전수는 아래 집계가 따로 센다.
    qc_by = await _qc_checked_by_map(db, ids)
    qc_running = await qc_backlog.running_ids(db, ids)

    items = [
        _completed_row(
            w, v, c, approved.get(w.work_item_id), qc_by.get(w.work_item_id),
            qc_running=w.work_item_id in qc_running,
        )
        for w, v, c in rows
    ]
    if not summary:
        return CompletedPage(
            total=total, items=items,
            filter_scanned=filter_scanned, filter_candidates=filter_candidates,
        )
    # 검색 전 전수. 납품 대상 집계의 분모이자 상한 판단의 기준이며 필터와 무관하다.
    # 거른 뒤의 `total`을 쓰면 목록 머리의 수가 고른 필터에 따라 바뀌어 분모로 쓸 수 없다.
    total_all = (
        total
        if completed_filter == "all" and like is None
        else await _completed_total(db, project_id)
    )
    agg = await _completed_aggregates(db, project_id, total_all)
    return CompletedPage(
        total=total,
        items=items,
        filter_scanned=filter_scanned,
        filter_candidates=filter_candidates,
        deliverable_total=agg.deliverable_total,
        deliverable_scan_limit=agg.deliverable_scan_limit,
        qc_held_total=agg.qc_held_total,
        qc_unreviewed_total=agg.qc_unreviewed_total,
    )


async def _completed_total(db: AsyncSession, project_id: UUID) -> int:
    """검색 전 완료 전수를 센다. 목록 머리의 분모다."""
    return await db.scalar(
        select(func.count()).select_from(approved_rows_stmt(project_id).order_by(None).subquery())
    ) or 0


async def _completed_aggregates(
    db: AsyncSession, project_id: UUID, total_all: int
) -> CompletedSummary:
    """완료 목록 머리의 전수 집계를 만든다. 목록과 요약이 같은 함수로 센다."""
    # 납품 대상 집계는 검색과 무관하게 전수 기준이다. 검색 결과 안에서 세면 목록 머리의
    # 수가 검색어를 칠 때마다 바뀌어 납품 가능 규모를 보여 주지 못한다.
    # 건수와 길이는 같은 검사 한 번에서 센다. 따로 돌리면 두 수가 서로 다른 시점의 값이 된다.
    deliverable = await _deliverable_scan(db, project_id, total_all)
    return CompletedSummary(
        total=total_all,
        deliverable_total=deliverable[0] if deliverable else None,
        deliverable_seconds=round(deliverable[1], 3) if deliverable else None,
        deliverable_scan_limit=DELIVERABLE_SCAN_LIMIT,
        qc_held_total=await qc_held_count(db, project_id),
        # 내보내기 버튼 옆에 쓰는 상한 없는 전수(`qc_unreviewed_total` 참고).
        qc_unreviewed_total=await qc_unreviewed_total(db, project_id),
        last_approved_at=await _last_approved_at(db, project_id),
    )


async def _last_approved_at(db: AsyncSession, project_id: UUID) -> datetime | None:
    """완료 작업의 마지막 승인 판정 시각을 돌려준다. 승인 판정 기록이 없으면 `None`이다.

    완료 목록과 같은 행 집합(`approved_rows_stmt`)에서 찾는다.
    """
    done = approved_rows_stmt(project_id).order_by(None).with_only_columns(WorkItem.work_item_id)
    return await db.scalar(
        select(func.max(Review.reviewed_at)).where(
            Review.work_item_id.in_(done),
            Review.verdict.in_(("approve", "modify_and_approve")),
        )
    )


async def completed_summary(
    db: AsyncSession, project_id: UUID, user: User
) -> CompletedSummary:
    """완료 목록 머리의 전수 집계를 한 번에 돌려준다. 검색·페이지와 무관하다.

    권한은 목록과 같다(관리자와 검수자). 화면은 이 값을
    따로 받아 캐시하고, 목록은 `summary=false`로 불러 페이지마다 다시 세지 않는다.

    Raises:
        HTTPException: 프로젝트가 없거나(404 project_not_found) 볼 수 없는 역할인 경우
            (403 not_work_result_viewer).
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_work_result_viewer(db, project_id, user)
    return await _completed_aggregates(db, project_id, await _completed_total(db, project_id))


async def qc_held_count(db: AsyncSession, project_id: UUID) -> int:
    """캡션 품질검수가 보류한 건수를 센다. 완료 목록이 빼고 센 수와 같다.

    화면이 이 수를 함께 표시해야 사용자가 목록이 줄어든 이유를 알 수 있다.
    게이트 쿼리(`approved_rows_stmt`)에 파라미터를 추가하지 않고 따로 센다.
    게이트에 보류분을 포함하는 옵션을 두면 옵션이 켜진 채 호출될 때 보류분이 납품된다.
    """
    return await db.scalar(
        select(func.count())
        .select_from(WorkItem)
        .where(
            WorkItem.project_id == project_id,
            WorkItem.status == "approved",
            WorkItem.qc_status == "held",
        )
    ) or 0


async def qc_held_ids(db: AsyncSession, project_id: UUID) -> list[UUID]:
    """보류로 후보에서 빠진 승인분의 작업 식별자 목록을 돌려준다. 패키지 리포트가 이 목록을 싣는다.

    `qc_held_count`와 같은 조건을 쓴다. 수와 목록이 다르면 화면의 보류 수와 패키지의 제외
    수가 달라진다. 그러면 받는 쪽은 두 수를 모두 믿을 수 없다.
    게이트 쿼리에 파라미터를 추가하지 않는 이유는 `qc_held_count`와 같다.

    정렬은 후보 쿼리와 같은 전순서다(`submitted_at`은 NULL일 수 있다). 제외 목록이 상한에서
    잘려도 만들 때마다 같은 건이 실린다.
    """
    return list(await db.scalars(
        select(WorkItem.work_item_id)
        .where(
            WorkItem.project_id == project_id,
            WorkItem.status == "approved",
            WorkItem.qc_status == "held",
        )
        .order_by(WorkItem.submitted_at, WorkItem.work_item_id)
    ))


async def list_qc_held(db: AsyncSession, project_id: UUID, limit: int, offset: int):
    """보류함 목록을 돌려준다. 게이트가 뺀 건을 판정 근거와 함께 보여 준다.

    같은 건을 제출과 승인 때 두 번 검사하므로 각 건에는 최신 검사 실행만 붙인다.
    근거 없이 목록만 주면 운영자가 보류 이유를 확인하러 다시 조회해야 한다.

    모델 실패로 끝난 다시 검사는 실행 기록을 남기지 않고 `caption_qc.failed`(trigger `recheck`)
    감사 이벤트만 남긴다. 그 시각이 최신 실행보다 뒤면 작업별로 돌려준다. 화면은 행의 판정·지적이
    그 이전 실행의 결과라고 같은 행에 표시한다.

    Returns:
        (행 목록, 작업별 최신 실행, 작업별 최신 실행 뒤에 실패한 다시 검사 시각, 보류 전수).
    """
    from api_gateway.models import CaptionQcRun, DomainEvent

    stmt = (
        select(WorkItem, Video, Clip)
        .join(Video, WorkItem.video_id == Video.video_id)
        .outerjoin(Clip, WorkItem.clip_id == Clip.clip_id)
        .where(
            WorkItem.project_id == project_id,
            WorkItem.status == "approved",
            WorkItem.qc_status == "held",
        )
        .order_by(WorkItem.submitted_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).all()
    ids = [w.work_item_id for w, _v, _c in rows]
    latest: dict = {}
    if ids:
        runs = (await db.scalars(
            select(CaptionQcRun)
            .where(CaptionQcRun.work_item_id.in_(ids))
            .order_by(CaptionQcRun.created_at)
        )).all()
        for r in runs:      # 오래된 것부터 순회하므로 마지막이 최신으로 남는다
            latest[r.work_item_id] = r
    recheck_failed: dict = {}
    if ids:
        failed = (await db.execute(
            select(DomainEvent.target_id, func.max(DomainEvent.occurred_at))
            .where(
                DomainEvent.event_type == qc_backlog.FAILED_EVENT,
                DomainEvent.target_id.in_(ids),
                DomainEvent.payload["trigger"].astext == "recheck",
            )
            .group_by(DomainEvent.target_id)
        )).all()
        for wid, at in failed:
            run = latest.get(wid)
            if run is None or at > run.created_at:
                recheck_failed[wid] = at
    return rows, latest, recheck_failed, await qc_held_count(db, project_id)


async def _deliverable_scan(
    db: AsyncSession, project_id: UUID, total: int
) -> tuple[int, float] | None:
    """전수 기준 납품 가능 건수와 그 클립 길이 합(초)을 센다. 상한을 넘으면 `None`을 돌려준다.

    클립이 없는 작업(영상 단위)은 건수에는 들고 길이에는 더하지 않는다.

    `None`은 "세지 않음"이다(0과 구분한다). 화면은 그 경우 이 페이지의 수를 분모와 함께
    표시한다. `None`을 0으로 그리면 "하나도 나갈 수 없다"고 잘못 표시한다.

    판정이 행마다 pydantic 검증이라 전수를 세려면 모든 행을 payload째 적재해야 한다. 그러면
    목록을 한 번 여는 비용이 물량에 비례하므로 상한(`DELIVERABLE_SCAN_LIMIT`)을 둔다.
    규칙을 SQL로 하나 더 구현하면 같은 판정이 두 곳에 생겨 어긋날 수 있으므로 쓰지 않는다.
    """
    if total > DELIVERABLE_SCAN_LIMIT:
        return None
    rows = (await db.execute(approved_rows_stmt(project_id))).all()
    approved = await _approved_at_map(db, [w.work_item_id for w, _v, _c in rows])
    count, seconds = 0, 0.0
    for w, v, c in rows:
        if export_package.check_row(w, v, c, approved.get(w.work_item_id))[1]:
            continue
        count += 1
        if c is not None and c.start_seconds is not None and c.end_seconds is not None:
            seconds += float(c.end_seconds - c.start_seconds)
    return count, seconds


def _completed_row(
    w: WorkItem, v: Video, c: Clip | None, approved_at, qc_checked_by: str | None = None,
    *, qc_running: bool = False,
) -> CompletedRow:
    """완료 목록의 행 하나를 만든다. 매니페스트와 같은 데이터를 화면용으로 줄인 값이다.

    납품 가능 여부를 여기서 판정한다. 목록과 상세가 같은 함수를 쓰므로 둘의 판정이 항상 같다.
    목록에서도 패키지를 만들기 전에 몇 건이 담길지 알 수 있다.
    """
    payload = w.payload if isinstance(w.payload, dict) else {}
    cap = _caption_of(payload)
    # 삭제 표시한 문장은 세지 않는다(`live_sentences`). 사용자는 보고 있는 캡션과 이 수를 맞춰 본다.
    # 삭제한 문장까지 세면 수가 맞지 않고, 어느 쪽이 틀렸는지 화면에서 알 수 없다.
    sentences = caption.live_sentences(cap.brief_sentences)
    # 배열이 없으면 문자열을 나눠 센다. 배열이 기준이고 문자열은 파생 값이지만,
    # 옛 payload에는 문자열만 있다. 배열 길이만 세면
    # 화면은 같은 문자열을 나눠 여러 문장을 그리는데 배지는 `캡션 0`이 된다.
    counted = sentences or [{"text": t} for t in _split_sentences(cap.brief)]
    _m, blockers = export_package.check_row(w, v, c, approved_at)
    return CompletedRow(
        deliverable=not blockers,
        blockers=blockers,
        qc_checked_by=qc_checked_by,
        qc_running=qc_running,
        work_item_id=w.work_item_id,
        video_id=v.video_id,
        clip_id=c.clip_id if c else None,
        start_seconds=float(c.start_seconds) if c and c.start_seconds is not None else None,
        end_seconds=float(c.end_seconds) if c and c.end_seconds is not None else None,
        video_title=v.title,
        program_name=v.program_name,
        broadcaster=v.broadcaster,
        aired_at=v.aired_at,
        caption_brief=cap.brief,
        sentence_count=len(counted),
        rights_status=c.rights_status if c else None,
        submitted_at=w.submitted_at,
        approved_at=approved_at,
    )


async def get_completed(
    db: AsyncSession, project_id: UUID, work_item_id: UUID, user: User
) -> CompletedDetail:
    """완료된 클립 하나의 전체 정보를 돌려준다. 읽기 전용 확인 화면이 쓴다.

    행 집합은 목록·내보내기와 같은 쿼리(`approved_rows_stmt`)다. 그래서 여기서 열리는 건과
    패키지 후보가 항상 같고, 목록에 있는 건은 모두 열린다.

    요약·이력·판정을 한 번에 돌려준다. 세 번 나눠 요청하면 화면에 서로 다른 시점의 값이 섞인다.

    권한은 목록과 같다(관리자와 검수자).

    Raises:
        HTTPException: 프로젝트가 없거나(404 project_not_found), 볼 수 없는 역할이거나
            (403 not_work_result_viewer), 승인됐지만 캡션 품질검수가 보류한 경우(404 qc_held),
            승인분이 아닌 경우(404 not_completed).
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_work_result_viewer(db, project_id, user)

    row = (await db.execute(
        approved_rows_stmt(project_id).where(WorkItem.work_item_id == work_item_id)
    )).first()
    if row is None:
        # 승인분이 아니면 이 화면에서 열지 않는다. 진행 중인 작업을 열면 완료 화면에 미완료
        # 작업이 섞인다.
        # 승인됐는데 품질검수가 보류해 빠진 건은 별도 코드(`qc_held`)로 알린다. 같은 코드를 쓰면
        # 화면이 이미 승인한 사람에게 승인이 끝나지 않았다고 표시한다.
        held = await db.scalar(
            select(WorkItem.work_item_id).where(
                WorkItem.work_item_id == work_item_id,
                WorkItem.project_id == project_id,
                WorkItem.status == "approved",
                WorkItem.qc_status == "held",
            )
        )
        if held:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "qc_held")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_completed")
    w, v, c = row

    approved = await _approved_at_map(db, [w.work_item_id])
    payload = w.payload if isinstance(w.payload, dict) else {}
    # 반려 사유 이름은 현재 목록에서 읽는다. 이름을 고치면 이력도 새 이름으로 표시된다.
    catalog = reject_reasons.of_project(project.settings)

    revisions = list((await db.scalars(
        select(Label).where(Label.work_item_id == work_item_id).order_by(Label.submitted_at)
    )).all())
    reviews = list((await db.scalars(
        select(Review).where(Review.work_item_id == work_item_id).order_by(Review.reviewed_at)
    )).all())
    # 이력의 행위자 이름을 한 번에 모아 조회한다. UUID만 주면 화면이 사람을 표시할 수 없다.
    people = {lab.submitted_by for lab in revisions} | {r.reviewer_id for r in reviews}
    names = {
        uid: name for uid, name in (await db.execute(
            select(User.user_id, User.display_name).where(User.user_id.in_(people))
        )).all()
    } if people else {}

    return CompletedDetail(
        row=_completed_row(
            w, v, c, approved.get(w.work_item_id),
            qc_running=w.work_item_id in await qc_backlog.running_ids(db, [w.work_item_id]),
        ),
        source_start_offset=export_package.offset_of(v)[0],
        source_checksum=v.source_sha256,
        source_duration=v.duration_seconds,
        caption=_caption_of(payload),
        # 납품 메타에 실리는 값과 같게 영상에서 물려받은 값을 싣는다. 라벨러 입력
        # (`meta`)은 납품이 읽지 않으므로 화면에도 싣지 않는다. 실리지 않는 값을 보여 주면
        # 화면과 납품 내용이 달라진다.
        video_meta=video_meta.of_video(v),
        revisions=[
            CompletedRevision(
                label_id=lab.label_id,
                revision=i + 1,
                submitted_by=lab.submitted_by,
                submitted_by_name=names.get(lab.submitted_by),
                submitted_at=lab.submitted_at,
                diff_chars_count=lab.diff_chars_count,
                # 개정 체인의 첫 단계(AI 초안)는 첫 제출 행에 붙어 있다.
                ai_draft=lab.ai_draft,
                edit_distance=lab.edit_distance,
                draft_chars=lab.draft_chars,
            )
            for i, lab in enumerate(revisions)
        ],
        reviews=[
            CompletedReview(
                verdict=r.verdict, comment=r.comment,
                reasons=reject_reasons.refs(r.reason_codes, catalog),
                reviewer_id=r.reviewer_id, reviewer_name=names.get(r.reviewer_id),
                reviewed_at=r.reviewed_at,
            )
            for r in reviews
        ],
    )
