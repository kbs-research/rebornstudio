"""라벨링 작업의 쓰기. 작업 생성·닫기, 다음 작업 배정·제출·임시저장·신고를 다룬다.

보유자 확인과 역할 확인(`_load_my_claim`·`_load_working_claim`·`_require_*`)도 이 모듈에 있다.
쓰기 경로가 이 확인을 거친다. 조회(`queries`)와 검수(`work.review`)도 파사드로 가져다 쓴다.
판정 조건은 `classify`에서 가져오고 이 모듈에서 다시 구현하지 않는다.
"""
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import (
    RIGHTS_CLEARED,
    NextWorkResponse,
    WorkItemDTO,
    WorkSubmitRequest,
)
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import (
    Clip,
    Label,
    Project,
    ProjectMember,
    User,
    Video,
    WorkItem,
)
from api_gateway.models.work import WORK_CLOSED
from api_gateway.services import audit, authz, caption
from api_gateway.services.work import edit_rate
from api_gateway.services.work.service.classify import (
    _CLOSABLE,
    HELD_ORDER,
    _claimable_where,
    _held_by,
    reject_if_not_claimable,
)
from api_gateway.services.work.service.dto import _to_dto

_ANNOTATOR_ROLES = {"annotator", "reviewer", "project_admin"}



async def ensure_for_clip(db: AsyncSession, clip) -> WorkItem | None:
    """클립별 work_item을 만든다. rights_status가 cleared 계열일 때만 만든다.

    `clip`은 models.Clip 인스턴스다. import 순환을 피하려고 타입을 적지 않는다.
    멱등이다. 부분 unique 인덱스(clip_id가 있을 때 1개)가 보장한다.

    닫힌 작업이 있으면 반려 횟수 0인 새 작업으로 되살린다.
    부분 unique 인덱스 때문에 새 행을 만들 수 없고,
    닫힌 행을 그대로 돌려주면 작업이 큐에 돌아오지 않는다.
    라벨·검수 행은 그대로 남아 사람 지표의 근거가 된다.
    """
    if clip.rights_status not in RIGHTS_CLEARED:
        return None
    existing = await db.scalar(select(WorkItem).where(WorkItem.clip_id == clip.clip_id))
    if existing:
        if existing.status == WORK_CLOSED:
            existing.status = "available"
            existing.claimed_by = existing.claimed_at = existing.expires_at = None
            existing.review_claimed_by = existing.review_claimed_at = None
            existing.review_expires_at = None
            existing.draft = existing.review_draft = None
            existing.rejection_count = 0
            await db.flush()
        return existing
    item = WorkItem(
        video_id=clip.video_id,
        clip_id=clip.clip_id,
        project_id=clip.project_id,
        status="available",
    )
    db.add(item)
    await db.flush()
    return item



async def ensure_for_video(db: AsyncSession, video: Video) -> WorkItem | None:
    """업로드가 끝난 영상에 MAIU 디스패치 티켓(영상 단위 work_item)을 만든다.

    이 티켓은 사람이 가져가는 작업과 다르다. `dispatch`가 status='available' AND clip_id IS NULL
    조건으로 가져가고, 사람 큐에서는 빠진다(`_human_workable`).
    이미 클립이 있는 영상은 분석이 끝난 영상이라 티켓을 만들지 않는다.
    """
    if video.upload_status != "uploaded":
        return None
    from api_gateway.models import Clip
    has_clips = await db.scalar(
        select(Clip).where(Clip.video_id == video.video_id).limit(1)
    )
    if has_clips:
        return None
    existing = await db.scalar(select(WorkItem).where(WorkItem.video_id == video.video_id))
    if existing:
        return existing
    item = WorkItem(video_id=video.video_id, project_id=video.project_id, status="available")
    db.add(item)
    await db.flush()
    return item



async def close_items(
    db: AsyncSession,
    *where,
    reason: str,
    actor_user_id: UUID | None,
    project_id: UUID,
) -> int:
    """미완 작업을 닫는다. 닫힌 작업은 큐와 집계 모집단에서 모두 빠진다.

    비식별화 신고의 제외 처리(`work.deident_reports.resolve`)와
    영상 삭제(`media.video.soft_delete`)가 부르고, 호출하는 쪽은 조건만 넘긴다.

    상태와 함께 점유(`claimed_by`·`review_claimed_by`)와 초안도 비운다.
    점유를 남기면 닫힌 작업이 보유 목록에 남고 다음 작업이 그 작업을 계속 다시 연다.
    감사 기록은 작업 ID로 찾을 수 있도록 작업마다 한 건씩 남긴다.

    Args:
        *where: 닫을 대상을 고르는 조건(예: `WorkItem.clip_id == …`).
            `_CLOSABLE` 상태 조건은 이 함수가 붙인다.
        reason: 감사 payload의 `reason`에 들어가는 닫는 이유.
            기계가 거르는 코드이고 화면 문구로 쓰지 않는다
            (`deident_excluded` · `video_deleted`).

    Returns:
        닫은 작업 수. commit은 호출자가 한다. 닫기는 제외·삭제 판정의 일부라서
        이 함수가 commit하면 판정이 뒤에서 실패해도 작업만 닫힌 채 남는다.
    """
    items = (await db.scalars(
        select(WorkItem).where(*where, WorkItem.status.in_(_CLOSABLE))
    )).all()
    for item in items:
        item.status = WORK_CLOSED
        item.claimed_by = item.claimed_at = item.expires_at = None
        item.review_claimed_by = item.review_claimed_at = item.review_expires_at = None
        item.draft = item.review_draft = None
        audit.emit(
            db, event_type="work.closed", target_kind="work_item",
            target_id=item.work_item_id, actor_user_id=actor_user_id,
            project_id=project_id,
            payload={
                "reason": reason,
                "video_id": str(item.video_id),
                "clip_id": str(item.clip_id) if item.clip_id else None,
            },
        )
    return len(items)



async def count_closable(db: AsyncSession, *where) -> int:
    """`close_items`가 지금 닫을 작업 수를 센다. 확인 창이 이 수를 표시한다.

    확인 창의 수와 실제로 닫히는 수가 같도록 `close_items`와 같은 `_CLOSABLE`을 쓴다.
    """
    return int(await db.scalar(
        select(func.count()).select_from(WorkItem)
        .where(*where, WorkItem.status.in_(_CLOSABLE))
    ) or 0)



async def retire_video_ticket(db: AsyncSession, video_id: UUID) -> int:
    """클립이 생긴 영상의 영상 티켓(MAIU 디스패치 대기 표시)을 지운다.

    `ensure_for_video`의 반대 동작이다.
    남겨 두면 디스패치 풀(status='available' AND clip_id IS NULL)과
    큐 상태 집계에 계속 잡힌다.
    상태를 바꾸지 않고 행을 지운다. 종료 상태 값(`approved` 등)으로 바꾸면
    `work_by_status` 집계를 거쳐 대시보드 처리량에 더해진다.
    클립이 아직 없는 영상의 티켓은 분석 대기 중이라 지우지 않는다. commit은 호출자가 한다.

    Returns:
        삭제된 행 수(0 또는 1).
    """
    if not await db.scalar(select(Clip.clip_id).where(Clip.video_id == video_id).limit(1)):
        return 0
    res = await db.execute(
        delete(WorkItem).where(WorkItem.video_id == video_id, WorkItem.clip_id.is_(None))
    )
    return res.rowcount or 0


async def next_work(db: AsyncSession, project_id: UUID, user: User) -> NextWorkResponse:
    """호출한 사용자의 다음 라벨링 작업 한 건을 돌려준다. 보유한 작업이 있으면 그 작업을 다시 준다.

    보유한 작업이 있으면 새 작업을 배정하지 않고 `HELD_ORDER`의 첫 건을 `resumed=True`로 돌려준다.
    반려된 재작업은 원 작업자에게 돌아오므로 한 사람이 잠시 두 건을 보유할 수 있다.

    보유한 작업이 없을 때만 대기열에서 한 건을 배정한다(FOR UPDATE SKIP LOCKED).
    큐로 돌아온 재작업을 만든 사람 외의 사용자가 받으면 반려 횟수를 0으로 두고 새 작업으로 시작한다.

    같은 사람의 동시 요청(두 번 누르기·두 탭)은 멤버 행 잠금으로 순서를 정한다.
    잠그지 않으면 두 요청이 모두 보유 없음을 보고 두 건을 배정한다.
    멤버가 아닌 super_admin은 잠글 행이 없다.

    Raises:
        HTTPException: 라벨링 역할이 없는 경우(403 not_annotator),
            프로젝트가 없는 경우(404 project_not_found),
            보유한 작업도 배정할 작업도 없는 경우(404 no_work_available),
            보유한 작업이 없고 프로젝트가 멈췄거나 끝난 경우(409 project_paused·project_not_active).
    """
    await _require_annotator(db, project_id, user)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await db.scalar(
        select(ProjectMember)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.user_id,
        )
        .with_for_update()
    )
    held = await db.scalar(
        select(WorkItem).where(*_held_by(project_id, user.user_id)).order_by(*HELD_ORDER).limit(1)
    )
    if held is not None:
        # 다시 연 것은 배정이 아니라서 감사 기록을 남기지 않는다. 배정은 처음 한 번만 기록한다.
        await db.commit()
        return _next_dto(held, resumed=True)

    reject_if_not_claimable(project)
    now = datetime.now(UTC)
    stmt = (
        select(WorkItem)
        .where(*_claimable_where(project_id))
        # 반려 맥락(사유·지적 문장)을 아는 사람이 재작업을 받도록
        # 호출한 사용자가 제출했던 작업(`labels.submitted_by`)을 먼저 고른다.
        # 그다음은 오래된 순이고 `work_item_id`로 순서를 고정한다.
        .order_by(
            exists().where(
                (Label.work_item_id == WorkItem.work_item_id)
                & (Label.submitted_by == user.user_id)
            ).desc(),
            WorkItem.created_at,
            WorkItem.work_item_id,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    item = await db.scalar(stmt)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no_work_available")

    # 초안이 남아 있으면 다음 라벨러의 워크벤치에 이전 사람의 입력이 되살아난다.
    item.draft = None
    # 만든 사람이 아닌 사용자가 재작업을 받으면 반려 횟수를 0으로 두어 새 작업으로 시작한다.
    # 새 보유자는 이전 사람의 제출본과 판정을 받지 않는다(`_work_view_scope`).
    # 판정 기록(`reviews`)은 지우지 않으므로 사람 지표와 납품 이력은 그대로다.
    reset_rejections: int | None = None
    if item.rejection_count > 0:
        maker = await db.scalar(
            select(Label.submitted_by)
            .where(Label.work_item_id == item.work_item_id)
            .order_by(Label.submitted_at.desc())
            .limit(1)
        )
        if maker != user.user_id:
            reset_rejections = item.rejection_count
            item.rejection_count = 0
    item.status = "claimed"
    item.claimed_by = user.user_id
    item.claimed_at = now
    item.expires_at = None
    # 작업에 걸린 시간을 확인할 수 있도록 배정도 기록한다.
    claimed_payload: dict = {}
    if reset_rejections is not None:
        claimed_payload["previous_rejections"] = reset_rejections
    audit.emit(
        db, event_type="work.claimed", target_kind="work_item",
        target_id=item.work_item_id, actor_user_id=user.user_id,
        project_id=item.project_id, payload=claimed_payload,
    )
    await db.commit()
    await db.refresh(item)
    return _next_dto(item, resumed=False)



def _next_dto(item: WorkItem, *, resumed: bool) -> NextWorkResponse:
    """작업 행을 다음 작업 응답으로 바꾼다. 라벨링과 검수가 같은 모양을 쓴다."""
    return NextWorkResponse(
        work_item_id=item.work_item_id,
        project_id=item.project_id,
        video_id=item.video_id,
        clip_id=item.clip_id,
        resumed=resumed,
    )



async def release_holds(
    db: AsyncSession, user_id: UUID, *, project_id: UUID | None = None,
) -> dict[str, list[UUID]]:
    """한 사람이 보유한 라벨링·검수 작업을 대기열로 되돌린다.

    보유에는 자동 회수가 없어서, 일을 할 수 없게 된 사람의 보유를 풀지 않으면 그 작업은 멈춘다.
    그래서 멤버 제거·나가기·계정 정지가 이 함수를 부른다.

    라벨링 보유는 `available`로 되돌리고 점유와 임시저장을 비운다.
    반려 횟수는 그대로 두어 반려 누적 목록에 남긴다.
    검수 보유는 검수 점유와 판정 초안만 비워 검수 대기(`submitted`)로 되돌린다.
    초안을 비워 다음 사람이 이전 사람의 입력을 물려받지 않게 한다.

    Args:
        user_id: 보유를 풀 사람.
        project_id: 이 프로젝트의 보유만 푼다. 없으면 모든 프로젝트의 보유를 푼다(계정 정지).

    Returns:
        푼 작업 ID 목록. `labeling`·`review` 두 키다. commit과 감사 기록은 호출자가 한다.
        풀린 이유(제거·나가기·정지)는 호출자의 이벤트에 남는다.
    """
    scope = (WorkItem.project_id == project_id,) if project_id is not None else ()
    labeling = (await db.execute(
        update(WorkItem)
        .where(*scope, WorkItem.claimed_by == user_id, WorkItem.status == "claimed")
        .values(status="available", claimed_by=None, claimed_at=None, expires_at=None, draft=None)
        .returning(WorkItem.work_item_id)
        .execution_options(synchronize_session=False)
    )).scalars().all()
    review = (await db.execute(
        update(WorkItem)
        .where(*scope, WorkItem.review_claimed_by == user_id, WorkItem.status == "submitted")
        .values(review_claimed_by=None, review_claimed_at=None, review_expires_at=None,
                review_draft=None)
        .returning(WorkItem.work_item_id)
        .execution_options(synchronize_session=False)
    )).scalars().all()
    return {"labeling": list(labeling), "review": list(review)}


def released_payload(released: dict[str, list[UUID]]) -> dict:
    """`release_holds` 결과를 감사 payload 조각으로 바꾼다.

    멤버 제거·나가기·계정 정지가 같은 모양을 쓴다.
    """
    return {
        "released": {k: [str(i) for i in v] for k, v in released.items()},
        "released_counts": {k: len(v) for k, v in released.items()},
    }



async def submit(
    db: AsyncSession, work_item_id: UUID, payload: WorkSubmitRequest, user: User
) -> WorkItemDTO:
    """Label revision을 저장하고 작업을 submitted로 바꾼다.

    작업의 첫 제출이면 라벨러가 받은 AI 초안과 초안 대비 수정량을 라벨 행에 함께 남긴다.
    이 값이 수정률과 개정 체인의 첫 단이 된다. 규칙은 `edit_rate` 한 곳에 있다.

    Raises:
        HTTPException: 작업이 없는 경우(404 work_item_not_found),
            제출할 수 없는 상태인 경우(409 work_item_is_{status}),
            호출한 사용자의 점유가 아닌 경우(403 not_your_claim),
            프로젝트 멤버가 아닌 경우(403 not_a_member),
            캡션이 `caption_min_tokens`에 못 미치는 경우(422 caption_too_short).
    """
    # 행을 잠그고 읽는다(`_load_my_claim`). 같은 작업의 두 번째 제출은 첫 제출이 커밋될 때까지
    # 기다렸다가 `submitted` 상태를 보고 409로 끝난다.
    item = await _load_working_claim(db, work_item_id, user)

    # 캡션 제출 게이트. 화면과 같은 `caption_min_tokens` 기준을 서버도 지킨다.
    # 서버가 검증하지 않으면 API를 직접 호출해 빈 캡션을 제출할 수 있다.
    # 기준 컬럼은 `NOT NULL DEFAULT 50`이고, 프로젝트를 읽지 못하면 화면과 같은 기본값을 쓴다.
    project = await db.get(Project, item.project_id)
    min_tokens = project.caption_min_tokens if project else caption.DEFAULT_MIN_TOKENS
    # 판정은 `services.caption` 한 곳에서 한다.
    # 서버가 화면보다 엄하면 화면에서 통과한 제출이 거절된다.
    if caption.too_short(payload.payload, min_tokens) is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "caption_too_short")

    # revision chain의 직전 Label을 찾는다. 있으면 parent가 되고 없으면 NULL이다.
    prev = await db.scalar(
        select(Label)
        .where(Label.work_item_id == work_item_id)
        .order_by(Label.submitted_at.desc())
        .limit(1)
    )
    prev_chars = len(str(prev.label_data)) if prev else 0
    new_chars = len(str(payload.payload))
    # 수정률은 작업의 첫 제출만 잰다. 재제출까지 재면 같은 교정이 두 번 세어진다(`edit_rate`).
    # 초안은 화면이 보낸 값 대신 서버가 클립에서 읽고, 초안이 없으면 재지 않는다.
    measured = None
    if prev is None and item.clip_id is not None:
        clip = await db.get(Clip, item.clip_id)
        measured = edit_rate.measure(clip.auto_caption if clip else None, payload.payload)
    label = Label(
        work_item_id=work_item_id,
        parent_label_id=prev.label_id if prev else None,
        submitted_by=user.user_id,
        label_data=payload.payload,
        diff_chars_count=abs(new_chars - prev_chars),
        ai_draft=measured.draft if measured else None,
        edit_distance=measured.distance if measured else None,
        draft_chars=measured.draft_chars if measured else None,
    )
    db.add(label)

    item.payload = payload.payload  # 작업 행에도 최신 제출 스냅숏을 둔다
    item.status = "submitted"
    item.submitted_at = datetime.now(UTC)
    # 제출하면 임시저장을 비운다. 재작업으로 다시 열면 직전 제출 라벨(label_data)로 캡션을 복원한다.
    item.draft = None

    # 라벨 행을 열지 않고 감사 기록만으로 제출 내용을 확인할 수 있게 수치를 함께 남긴다.
    audit.emit(
        db, event_type="work.submitted", target_kind="work_item",
        target_id=item.work_item_id, actor_user_id=user.user_id,
        project_id=item.project_id,
        payload={
            "first_submission": prev is None,  # 반려 후 재제출이면 False
            "diff_chars": label.diff_chars_count,
            "chars": new_chars,
            # 수정률의 재료. 잰 제출에만 값이 있다. None은 재지 않았다는 뜻이다(0과 구분한다).
            "edit_distance": label.edit_distance,
            "draft_chars": label.draft_chars,
        },
    )
    # 제출 검사는 커밋 뒤 워커가 이 라벨 행을 읽어 실행한다(`caption_qc.backlog`).
    # 요청 안에서 모델 응답을 기다리면 그동안 커넥션과 행 잠금을 점유하고,
    # 그 사이 api가 재시작되면 제출까지 롤백된다.
    await db.commit()
    await db.refresh(item)
    return _to_dto(item)



async def save_draft(
    db: AsyncSession, work_item_id: UUID, payload: dict, user: User
) -> WorkItemDTO:
    """부분 payload를 draft 컬럼에 임시저장한다.

    제출과 별개로 보유자만 쓸 수 있다(`_load_working_claim`).
    """
    item = await _load_working_claim(db, work_item_id, user)
    item.draft = payload
    await db.commit()
    await db.refresh(item)
    return _to_dto(item)



async def report_deident_miss(
    db: AsyncSession, work_item_id: UUID, timestamp_ms: int, reason: str, user: User
) -> None:
    """비식별화 미흡을 신고한다. 클립을 legal_review로 보내고 그 작업을 멈춘다.

    라벨링 보유자나 검수 보유자가 호출한다. 검수 대상은 `submitted` 상태이고 점유가
    `review_claimed_by`에 있다. 그래서 `_load_working_claim`(status=claimed)만 쓰면
    검수 쪽 신고가 409가 된다.

    신고한 사람의 보유를 푼다. 보유를 남기면 다음 작업이 멈춘 작업을 계속 다시 연다.
    작업은 `_human_workable`이 클립 상태를 보고 큐에서 빼므로 다른 사람에게도 가지 않는다.
    라벨링 점유면 작업을 `available`로 되돌리고 점유와 임시저장을 비운다.
    검수 점유면 검수 점유와 판정 초안만 비우고 제출 상태는 그대로 둔다.
    보유를 푼 사실은 `work.released`·`review.released`(`cause` 포함)로 남긴다.
    이후 처리(재개·제외)는 `work.deident_reports.resolve`가 한다.

    Raises:
        HTTPException: 작업이 없는 경우(404 work_item_not_found),
            클립 단위 작업이 아닌 경우(409 no_clip),
            호출한 사용자의 점유가 아닌 경우(403 not_your_claim).
    """
    item = await lock_item(db, work_item_id)
    holds_review = item.status == "submitted" and item.review_claimed_by == user.user_id
    if holds_review:
        await authz.require_participation(db, item.project_id, user)
    else:
        item = await _load_working_claim(db, work_item_id, user)
    if not item.clip_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_clip")
    clip = await db.get(Clip, item.clip_id)
    clip.rights_status = "legal_review"
    audit.emit(
        db, event_type="clip.deident_miss_reported", target_kind="clip",
        target_id=clip.clip_id, actor_user_id=user.user_id, project_id=item.project_id,
        payload={"timestamp_ms": timestamp_ms, "reason": reason},
    )
    if holds_review:
        item.review_claimed_by = item.review_claimed_at = item.review_expires_at = None
        item.review_draft = None
        event = "review.released"
    else:
        item.status = "available"
        item.claimed_by = item.claimed_at = item.expires_at = None
        item.draft = None
        event = "work.released"
    audit.emit(
        db, event_type=event, target_kind="work_item",
        target_id=item.work_item_id, actor_user_id=user.user_id,
        project_id=item.project_id,
        payload={"cause": "deident_miss_reported"},
    )
    await db.commit()



async def _require_work_viewer(db: AsyncSession, item: WorkItem, user: User) -> None:
    """본인 claim, 프로젝트 reviewer 이상 역할, super_admin만 통과시키고 나머지는 403을 낸다.

    본인 claim은 참여 여부와 관계없이 통과한다.
    워크벤치가 보유가 끝난 작업도 읽어 표시하기 때문이다.
    다른 사람의 작업을 보는 경우만 역할을 확인한다.
    """
    if user.global_role == "super_admin":
        return
    if item.claimed_by == user.user_id:
        return
    member = await db.get(ProjectMember, (item.project_id, user.user_id))
    if not member or member.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_a_member")
    await authz.require_role(
        db, item.project_id, user, ("reviewer", "project_admin"), detail="not_reviewer"
    )



async def _work_view_scope(db: AsyncSession, item: WorkItem, user: User) -> UUID | None:
    """작업 조회 권한을 확인하고, 보유자 자격으로만 보는 사용자면 그 사용자의 ID를 돌려준다.

    작업은 재투입이나 멤버 제거로 다른 라벨러에게 넘어간다. 새 보유자가 이전 라벨러의
    제출본과 반려 사유를 받지 않도록 보유자는 자기 제출과 그 판정만 본다.
    검수자·관리자는 판단 근거로 전체 이력을 본다.

    Returns:
        전체 이력을 볼 수 있으면 None, 보유자 자격으로만 보면 호출자의 user_id.

    Raises:
        HTTPException: 볼 권한이 없는 경우(`_require_work_viewer`).
    """
    if user.global_role == "super_admin":
        return None
    if item.claimed_by == user.user_id:
        role = await authz.my_role(db, item.project_id, user)
        return None if role in ("reviewer", "project_admin") else user.user_id
    await _require_work_viewer(db, item, user)
    return None



async def _load_working_claim(db: AsyncSession, work_item_id: UUID, user: User) -> WorkItem:
    """`_load_my_claim`에 참여 여부 확인을 더한다. 산출물을 만드는 액션이 쓴다.

    제출·임시저장·비식별화 신고는 액션 시점에 프로젝트 참여자여야 한다.
    본인 claim인지만 보면 멤버에서 빠진 뒤에도 전에 받은 claim으로 계속 제출할 수 있다.
    보유에는 끝나는 시각이 없으므로 시각은 확인하지 않는다.

    Raises:
        HTTPException: `_load_my_claim`이 내는 경우,
            참여자가 아닌 경우(403 not_a_member).
    """
    item = await _load_my_claim(db, work_item_id, user)
    await authz.require_participation(db, item.project_id, user)
    return item



async def _load_my_claim(db: AsyncSession, work_item_id: UUID, user: User) -> WorkItem:
    """작업을 불러오고 claimed 상태이며 호출자가 보유자인지 확인한다.

    super_admin도 면제하지 않는다. 면제하면 다른 사람의 초안을 덮어쓰거나,
    다른 사람의 claim에 호출한 사용자 이름으로 라벨을 남겨 제출량 지표가 틀린다.
    검수 쪽 `_require_review_holder`도 같은 규칙이다.

    행을 잠그고 읽는다(`lock_item`). 늦게 온 요청은 먼저 끝난 요청이 바꾼 상태를 보고 판정한다.

    Raises:
        HTTPException: 작업이 없는 경우(404 work_item_not_found),
            claimed 상태가 아닌 경우(409 work_item_is_{status}),
            호출한 사용자의 점유가 아닌 경우(403 not_your_claim).
    """
    item = await lock_item(db, work_item_id)
    if item.status != "claimed":
        raise HTTPException(status.HTTP_409_CONFLICT, f"work_item_is_{item.status}")
    if item.claimed_by != user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_your_claim")
    return item



async def lock_item(db: AsyncSession, work_item_id: UUID) -> WorkItem:
    """작업 행을 잠그고 최신 값으로 읽는다. 같은 작업을 바꾸는 요청을 차례로 처리한다.

    제출·판정·멤버 제거가 같은 행을 바꾼다. 잠그지 않으면 두 요청이 같은 상태를 보고 둘 다 통과해
    두 번 기록되거나, 늦게 커밋한 요청이 먼저 끝난 요청의 결과를 덮어쓴다.
    `populate_existing`은 세션에 먼저 읽힌 같은 행이 있어도 잠근 뒤의 값으로 다시 채운다.
    잠금은 요청 트랜잭션이 커밋되거나 롤백될 때 풀린다.

    Raises:
        HTTPException: 작업이 없는 경우(404 work_item_not_found).
    """
    item = await db.scalar(
        select(WorkItem)
        .where(WorkItem.work_item_id == work_item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "work_item_not_found")
    return item



async def _require_annotator(db: AsyncSession, project_id: UUID, user: User) -> None:
    """annotator / reviewer / project_admin / super_admin 권한이 없으면 403을 낸다."""
    await authz.require_role(db, project_id, user, _ANNOTATOR_ROLES, detail="not_annotator")
