"""품질검수 보류를 사람이 푸는 기능.

캡션을 고쳐 다시 승인, 다시 검사, 보류 해제의 세 기능을 제공한다.
승인 뒤 품질검수가 보류하면 그 작업은 납품에서 빠진다(`qc_status='held'`).
비속어 검사는 부분 문자열 매칭이라 정당한 인용도 걸린다. 보류를 푸는 방법이 캡션 수정뿐이면
오탐 한 건이 납품에서 계속 빠지거나, 검사를 통과하려고 캡션을 고치게 된다.

캡션 QC의 원칙은 "자동 검사는 보류까지, 판정은 사람"이다. 해제에는 사유가 필수이고,
해제 시점의 지적 목록 전체가 감사 기록에 들어간다.

- 캡션을 고쳐 다시 승인: 캡션 자체가 틀렸을 때 쓴다(개인정보·잘못된 표기). 고친 버전을 새 라벨
  개정과 승인 판정으로 남기고 게이트를 `pending`으로 되돌린다. 승인 검사는 워커가 다른 승인과
  같은 경로로 다시 하고 그 결과가 게이트를 정한다.
- 보류 해제: 사전이 틀렸다고 사람이 판단할 때 쓴다. 다시 실행해도 결과가 같으므로 검사는
  다시 실행하지 않고 `qc_status='passed'`로 바꾼다.
- 다시 검사: 사전이나 설정을 고친 뒤 쓴다. 승인 절차 없이 검사만 다시 실행한다.
  통과하면 보류가 풀리고, 다시 걸리거나 AI 검사를 호출하지 못하면 보류를 유지한다.

셋 다 납품 게이트를 바꾸는 기능이라 `project_admin`만 호출할 수 있다.
"""
import copy
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import QcHoldActionResult, QcSentenceEdit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import CaptionQcRun, Label, Project, Review, User, WorkItem
from api_gateway.services import audit, authz, caption
from api_gateway.services.caption_qc import backlog, runner


async def _load(
    db: AsyncSession, project_id: UUID, work_item_id: UUID, user: User
) -> WorkItem:
    """대상 작업을 불러오고 권한, 소속 프로젝트, 보류 여부를 확인한다.

    권한 검사는 경로의 프로젝트를 기준으로 하므로 경로의 `project_id`가 작업의 프로젝트와 같은지도
    확인한다. 이 확인이 없으면 다른 프로젝트의 관리자가 자기 프로젝트 경로로 보류를 풀 수 있다.

    Raises:
        HTTPException: 작업이 없거나(404 work_item_not_found), 관리자가 아니거나
            (403 not_project_admin), 보류 상태가 아닌 경우(409 qc_not_held).
    """
    await authz.require_project_admin(db, project_id, user)
    item = await db.get(WorkItem, work_item_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "work_item_not_found")
    if item.qc_status != "held":
        # 이미 풀린 보류를 다시 풀려는 요청은 화면 목록이 오래된 경우라 409를 돌려준다.
        raise HTTPException(status.HTTP_409_CONFLICT, "qc_not_held")
    return item


async def _latest_run(db: AsyncSession, work_item_id: UUID) -> CaptionQcRun | None:
    """작업의 가장 최근 실행을 돌려준다. 해제 기록에 붙일 지적 목록을 여기서 읽는다."""
    return await db.scalar(
        select(CaptionQcRun)
        .where(CaptionQcRun.work_item_id == work_item_id)
        .order_by(CaptionQcRun.created_at.desc())
        .limit(1)
    )


async def release(
    db: AsyncSession, project_id: UUID, work_item_id: UUID, reason: str, user: User
) -> QcHoldActionResult:
    """보류를 풀고 사유와 해제 시점의 지적 목록을 감사 기록에 남긴다.

    지적 목록은 감사 payload에 모두 넣는다. 실행 기록(`caption_qc_runs`)은 지워질 수 있고
    사전을 고친 뒤 다시 검사하면 다른 지적이 나와서, 해제할 때 본 지적을 나중에 복원할 수 없다.
    다시 실행해도 같은 결과가 나오는 오탐에 쓰는 기능이라 검사는 다시 실행하지 않는다.

    Raises:
        HTTPException: 작업이 없거나(404 work_item_not_found), 관리자가 아니거나
            (403 not_project_admin), 보류 상태가 아닌 경우(409 qc_not_held).
    """
    item = await _load(db, project_id, work_item_id, user)
    run = await _latest_run(db, work_item_id)
    findings = list(run.findings or []) if run else []
    item.qc_status = "passed"
    audit.emit(
        db, event_type="caption_qc.hold_released", target_kind="work_item",
        target_id=item.work_item_id, actor_user_id=user.user_id,
        project_id=item.project_id,
        payload={
            "reason": reason,
            "verdict_at_release": run.verdict if run else None,
            "findings_at_release": findings,
        },
    )
    await db.commit()
    return QcHoldActionResult(
        work_item_id=item.work_item_id, qc_status="passed", findings=len(findings),
    )


async def recheck(
    db: AsyncSession, project_id: UUID, work_item_id: UUID, user: User
) -> QcHoldActionResult:
    """검사만 다시 실행한다. 사전이나 설정을 고친 뒤 쓴다.

    승인 절차는 다시 밟지 않고 검사 시점을 `recheck`로 남긴다. 러너는 `approve`와 `recheck`에서
    게이트를 바꾼다(`models.work.CAPTION_QC_GATE_TRIGGERS`).
    `submit`으로 실행하면 보류가 바뀌지 않고,
    `approve`로 실행하면 승인이 없던 시각에 승인 검사가 있었던 것처럼 기록된다.

    통과하면 보류가 풀리고, 다시 걸리면 보류를 유지한다. 사람이 푸는 기능은 `release`다.
    검사가 꺼져 있거나 실패해 `runner.run`이 `None`을 돌려주면 감사 기록에 `ran`을 거짓으로 남기고
    판정 없이 응답한다.

    모델 호출이 실패하면 실행 기록을 남기지 않고 보류를 유지한다(`model_failed`).
    규칙만으로 확정하면
    모델이 보지 못한 승인본이 사람 판정 없이 납품 대상이 된다. 사람이 결과를 보고 다시 시도할 수
    있으므로 한 번만 시도한다. 실패한 시도는 워커와 같은 `caption_qc.failed` 감사 이벤트
    (`trigger=recheck`)로 남는다.

    Raises:
        HTTPException: 작업이 없거나(404 work_item_not_found), 관리자가 아니거나
            (403 not_project_admin), 보류 상태가 아닌 경우(409 qc_not_held).
    """
    item = await _load(db, project_id, work_item_id, user)
    try:
        run = await runner.run(db, work_item_id, trigger="recheck", model_final=False)
        model_failed = False
    except runner.ModelRetry:
        run, model_failed = None, True
        backlog.record_failure(db, item, "recheck", "model_failed")
    verdict = run.verdict if run else None
    findings = len(run.findings or []) if run else 0
    audit.emit(
        db, event_type="caption_qc.rechecked", target_kind="work_item",
        target_id=item.work_item_id, actor_user_id=user.user_id,
        project_id=item.project_id,
        payload={"verdict": verdict, "findings": findings, "ran": run is not None,
                 "model_failed": model_failed},
    )
    await db.commit()
    await db.refresh(item)
    return QcHoldActionResult(
        work_item_id=item.work_item_id, qc_status=item.qc_status,
        verdict=verdict, findings=findings, model_failed=model_failed,
    )


async def revise(
    db: AsyncSession, project_id: UUID, work_item_id: UUID,
    edits: list[QcSentenceEdit], user: User,
) -> QcHoldActionResult:
    """보류된 승인본의 캡션을 고쳐 다시 승인한다. 승인 검사는 워커가 다시 한다.

    검수자의 `수정 후 승인`과 같은 형식으로 남긴다. 고친 버전은 새 라벨 개정(부모는 직전 개정)이고,
    같은 개정을 가리키는 `modify_and_approve` 판정을 관리자 이름으로 더한다.
    판정 시각이 승인 검사의 기준 시각이 되므로 워커가 다른 승인과 같은 경로로 검사한다
    (`caption_qc.backlog`). 게이트는 `pending`으로 되돌리고, 내보내기는 승인 검사를 기다린다.

    고칠 문장만 받아 같은 id 문장의 본문만 바꾼다. 구간·삭제 표시와 다른 라벨은 그대로다.
    관리자도 제출 게이트와 같은 기준(`caption.too_short`)보다 짧은 캡션은 저장할 수 없다.
    수정 시점의 지적 목록과 고친 문장 id를 감사 기록에 남긴다.

    Raises:
        HTTPException: 작업이 없거나(404 work_item_not_found), 관리자가 아니거나
            (403 not_project_admin), 보류 상태가 아니거나(409 qc_not_held),
            승인본에 없는 문장 id를 보냈거나(422 sentence_not_found),
            바뀐 문장이 없거나(422 caption_unchanged), 고친 캡션이 제출 기준보다 짧은 경우
            (422 caption_too_short).
    """
    item = await _load(db, project_id, work_item_id, user)
    sents = [dict(x) for x in caption.sentences_of(item.payload)]
    by_id = {x.get("id"): x for x in sents}
    if any(e.id not in by_id for e in edits):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "sentence_not_found")
    changed = [e.id for e in edits if (by_id[e.id].get("text") or "").strip() != e.text]
    if not changed:
        # 같은 문장을 다시 승인하면 같은 지적에 다시 걸린다. 오탐이면 다시 검사나 보류 해제를 쓴다.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "caption_unchanged")
    for e in edits:
        by_id[e.id]["text"] = e.text

    payload = copy.deepcopy(item.payload or {})
    cap = payload.get("caption")
    cap = dict(cap) if isinstance(cap, dict) else {}
    cap["brief_sentences"] = sents
    cap["brief"] = " ".join(x["text"].strip() for x in caption.live_sentences(sents))
    payload["caption"] = cap
    project = await db.get(Project, item.project_id)
    min_tokens = project.caption_min_tokens if project else caption.DEFAULT_MIN_TOKENS
    if caption.too_short(payload, min_tokens) is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "caption_too_short")

    run = await _latest_run(db, work_item_id)
    prev = await db.scalar(
        select(Label).where(Label.work_item_id == work_item_id)
        .order_by(Label.submitted_at.desc()).limit(1)
    )
    label = Label(
        work_item_id=item.work_item_id,
        parent_label_id=prev.label_id if prev else None,
        submitted_by=user.user_id,
        label_data=payload,
        diff_chars_count=abs(len(str(payload)) - len(str(prev.label_data if prev else ""))),
    )
    db.add(label)
    await db.flush()  # 판정 행이 새 개정을 가리키려면 label_id가 먼저 있어야 한다
    db.add(Review(
        work_item_id=item.work_item_id, label_id=label.label_id, reviewer_id=user.user_id,
        verdict="modify_and_approve", reason_codes=[],
    ))
    item.payload = payload
    item.qc_status = "pending"
    audit.emit(
        db, event_type="caption_qc.hold_revised", target_kind="work_item",
        target_id=item.work_item_id, actor_user_id=user.user_id,
        project_id=item.project_id,
        payload={
            "label_id": str(label.label_id),
            "changed_sentences": changed,
            "verdict_at_revise": run.verdict if run else None,
            "findings_at_revise": list(run.findings or []) if run else [],
        },
    )
    await db.commit()
    return QcHoldActionResult(
        work_item_id=item.work_item_id, qc_status="pending",
        findings=len(run.findings or []) if run else 0,
    )
