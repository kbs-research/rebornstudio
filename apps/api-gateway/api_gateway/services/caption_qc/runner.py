"""캡션 QC 실행기. 재료를 모아 검사하고 결과를 남긴다.

트리거는 셋이고 검사 엔진은 하나다.

- 제출 직후(`submit`): 라벨러 제출본을 검사해 리뷰어 화면에 근거를 남긴다.
- 승인 직후(`approve`): 리뷰어가 고친 최종본을 다시 검사한다.
  리뷰어의 수정본도 검사를 거쳐 납품된다.
- 다시 검사(`recheck`): 관리자가 보류 화면에서 승인본을 다시 검사한다(`hold.recheck`).
  승인과 같은 게이트를 쓰지만 실행 기록에는 따로 남긴다.

제출과 승인은 검사를 기다리지 않고 먼저 커밋하고, 검사는 커밋 뒤 워커가 `run_owed`로 실행한다.
모델 응답은 수십 초 걸린다. 요청 안에서 기다리면 그동안 DB 커넥션과 행 잠금을 점유하고,
api가 재시작되면 제출·승인까지 롤백된다. 다시 검사는 관리자가 결과를 바로 보는 기능이라
요청 안에서 `run`을 호출한다.

검사 로직은 `rules`·`adapters`에 있다. 이 모듈은 재료 수집과 결과 처리만 한다.

QC가 실패해도 파이프라인을 막지 않는다. `run`은 모델 재시도 신호(`ModelRetry`) 외의 예외를
호출부로 올리지 않는다. 제출·승인은 사람이 한 일이라 검사 실패로 되돌리지 않는다.
모델 호출에 실패한 워커 실행은 기록하지 않고 `backlog.RETRY_SECONDS` 간격으로 다시 시도한다.
`backlog.MAX_FAILURES`번째 시도에서도 실패하면 `model_failed` 단계와 규칙 판정으로 확정한다.
실행 기록을 남기지 못한 건(DB 오류 등)도 같은 횟수로 센다.
다시 검사는 요청 안에서 한 번만 시도하고, 모델 호출에 실패하면 기록하지 않고 보류를 유지한다.
게이트 조건은 `!= held`라서 검사를 실행하지 않은 작업은 납품을 막지 않는다.
"""
from __future__ import annotations

import logging
import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import CaptionQcRun, Clip, Connection, Project, Video, WorkItem
from api_gateway.models.work import CAPTION_QC_GATE_TRIGGERS
from api_gateway.services import audit, caption, crypto, video_meta
from api_gateway.services.analysis import scene_options
from api_gateway.services.caption_qc import adapters, backlog, rules
from api_gateway.services.caption_qc import settings as qc_settings
from api_gateway.services.deident_gap import (
    DEIDENT_BEFORE_ON,
    DEIDENT_OFF,
    deident_gap,
    reached_deident,
)
from api_gateway.services.exports.export_package import check_row

log = logging.getLogger(__name__)


class ModelRetry(Exception):  # noqa: N818 — 실패가 아니라 다시 시도하라는 신호다
    """모델 호출에 실패해 이번 실행을 기록하지 않고 다시 시도해야 하는 경우의 신호."""


async def run(
    db: AsyncSession, work_item_id: UUID, trigger: str, *, model_final: bool = True,
) -> CaptionQcRun | None:
    """작업 한 건을 검사하고 결과를 남긴다. `ModelRetry` 외의 예외는 호출부로 올리지 않는다.

    `model_final`이 거짓이면 모델 호출 실패를 기록하지 않고 `ModelRetry`를 올린다.
    관리자의 다시 검사(`hold.recheck`)는 이 방식으로 호출해
    모델이 보지 못한 승인본을 규칙만으로 풀지 않는다.

    Returns:
        저장한 실행 기록. 검사가 꺼져 있거나 대상이 아니면 아무것도 남기지 않고 `None`이다.
        실행 중 예외가 나도 로그만 남기고 `None`이다.

    Raises:
        ModelRetry: `model_final`이 거짓이고 모델 호출에 실패한 경우.
    """
    try:
        return await _run(db, work_item_id, trigger, model_final=model_final)
    except ModelRetry:
        raise
    except Exception:  # noqa: BLE001 — 파이프라인을 막지 않는 것이 이 함수의 계약이다
        log.exception("캡션 QC 실행 실패 work_item=%s trigger=%s", work_item_id, trigger)
        return None


async def run_owed(db: AsyncSession, work_item_id: UUID, trigger: str) -> str:
    """커밋된 제출·승인 한 건을 검사하고 커밋한다. 워커 태스크 `caption_qc_run`이 부른다.

    실행 직전에 아직 검사를 기다리는지 다시 확인한다(`backlog.is_owed`). 대기열에 있는 동안
    검수자가 먼저 판정했거나 다른 실행이 먼저 끝났으면 아무것도 하지 않는다.
    쓰기 직전에는 작업 행을 잠그고 다른 실행이 이미 기록했는지 본다. 같은 건이 동시에 두 번
    실행돼도 실행 기록과 게이트 갱신은 하나만 남는다.

    모델 호출에 실패하면 마지막 시도가 아닌 한 기록하지 않고 롤백한다(`retry`). 일시 장애 한 번에
    모델이 보지 않은 캡션을 규칙 판정으로 확정하지 않기 위해서다. 마지막 시도(앞선 실패가
    `backlog.MAX_FAILURES - 1`번)에서도 실패하면 `model_failed` 단계와 규칙 판정으로 기록한다.

    실행 기록을 남기지 못했거나 모델 실패로 롤백하면 `caption_qc.failed` 감사 이벤트를 남긴다.
    투입 주기 태스크가 그 수를 세어 `backlog.MAX_FAILURES`번까지 `backlog.RETRY_SECONDS` 간격으로
    다시 보낸다.

    `ran`·`skipped`·`duplicate`로 끝나면 투입 표시를 지운다(`backlog.release`). 반려 뒤 바로 다시
    제출한 버전이 앞선 투입의 표시를 기다리지 않게 한다.

    Returns:
        `ran`(기록 완료), `skipped`(대상이 아님), `duplicate`(다른 실행이 먼저 기록),
        `retry`(모델 실패로 다시 시도), `failed`(실행 기록 실패) 중 하나.
    """
    result = await _run_once(db, work_item_id, trigger)
    if result in _SETTLED:
        backlog.release(work_item_id, trigger)
    return result


#: 이 시점의 검사를 더 하지 않아도 되는 실행 결과. 이 결과로 끝나면 투입 표시를 지운다.
_SETTLED = frozenset({"ran", "skipped", "duplicate"})


async def _run_once(db: AsyncSession, work_item_id: UUID, trigger: str) -> str:
    """실행 직전 확인부터 기록·실패 집계까지 한 번 실행한다. 결과 문자열은 `run_owed`와 같다."""
    if not await backlog.is_owed(db, work_item_id, trigger):
        await db.rollback()
        return "skipped"
    final = await backlog.failures_of(db, work_item_id, trigger) >= backlog.MAX_FAILURES - 1
    try:
        row = await _run(db, work_item_id, trigger, model_final=final)
        if row is None:
            await db.rollback()
            return "skipped"
        # 쓰기 전에 작업 행을 먼저 잠근다. 실행 행을 먼저 쓰면 외래 키 확인이 작업 행에 공유 잠금을
        # 걸어 동시에 도는 다른 실행과 교착이 난다.
        with db.no_autoflush:
            await db.execute(
                select(WorkItem.work_item_id)
                .where(WorkItem.work_item_id == work_item_id)
                .with_for_update()
            )
            if await backlog.recorded_by_other(db, work_item_id, trigger):
                await db.rollback()
                return "duplicate"
        await db.commit()
        return "ran"
    except ModelRetry:
        log.warning("캡션 QC 모델 호출 실패, 다시 시도 work_item=%s trigger=%s",
                    work_item_id, trigger)
        await _count_failure(db, work_item_id, trigger, "model_failed")
        return "retry"
    except Exception as e:  # noqa: BLE001 — 실패를 세어야 같은 건을 끝없이 다시 보내지 않는다
        log.exception("캡션 QC 실행 실패 work_item=%s trigger=%s", work_item_id, trigger)
        await _count_failure(db, work_item_id, trigger, type(e).__name__)
        return "failed"


async def _count_failure(db: AsyncSession, work_item_id: UUID, trigger: str, error: str) -> None:
    """이번 실행을 롤백하고 실패 한 번을 기록한 뒤 다시 보낼 시각을 앞당긴다."""
    await db.rollback()
    try:
        item = await db.get(WorkItem, work_item_id)
        if item is not None:
            backlog.record_failure(db, item, trigger, error)
            await db.commit()
    except Exception:  # noqa: BLE001 — 실패 기록마저 못 쓰면 다음 투입이 다시 시도한다
        log.exception("캡션 QC 실패 기록 실패 work_item=%s", work_item_id)
        await db.rollback()
    backlog.retry_after(work_item_id, trigger)


#: 모델이 검사하는 네 단계. 화면 체크리스트의 순서이기도 하다.
#: `adapters._ALLOWED_CHECKS`와 같은 집합이어야 하며 테스트가 이를 보장한다.
_MODEL_STAGES = ("spelling", "tone", "inappropriate", "glossary")


async def _run(
    db: AsyncSession, work_item_id: UUID, trigger: str, *, model_final: bool = True,
) -> CaptionQcRun | None:
    """규칙 검사, 모델 검사, 실행 기록 저장, 게이트 갱신을 차례로 한다. `run`의 본체다.

    `model_final`이 거짓이면 모델 호출 실패를 기록하지 않고 `ModelRetry`를 던진다.
    참이면 실패를 `model_failed` 단계로 남기고 규칙 판정으로 확정한다.

    Raises:
        ModelRetry: `model_final`이 거짓이고 모델 호출에 실패한 경우.
    """
    _t0 = time.monotonic()
    item = await db.get(WorkItem, work_item_id)
    if item is None:
        return None
    project = await db.get(Project, item.project_id)
    conf = qc_settings.of_project(project.settings if project else None)
    if not conf.enabled:
        return None   # QC를 켜지 않은 프로젝트(기본값). 아무것도 기록하지 않는다

    video = await db.get(Video, item.video_id)
    clip = await db.get(Clip, item.clip_id) if item.clip_id else None
    sentences = _sentences_of(item.payload)

    # 규칙 검사(모델 없이). 단계별 결과는 실행하면서 기록한다(0077).
    # `findings`만으로는 지적 0건인 통과와 실행하지 않은 단계를 구분할 수 없다.
    # 설정은 나중에 바뀌므로 실행 시점에 기록한다.
    opts = scene_options.of_project(project.settings if project else None)
    duration = (clip.end_seconds - clip.start_seconds) if clip else None
    deident_report = (video.extra or {}).get("deident") if video else None
    deident_reported = await _was_deident_reported(db, item.clip_id)

    stages: list[dict] = []

    def _ran(key: str, found: list[dict]) -> list[dict]:
        """실행한 검사 단계의 결과를 기록한다.

        걸린 지적이 있으면 `flagged`, 알림(`info`)만 있으면 `noted`, 지적이 없으면 `passed`다.
        판정(`verdict_of`)이 `info`를 세지 않으므로 알림만 있는 단계도 `flagged`로 두지 않는다.
        """
        if not found:
            status = "passed"
        elif all(f.get("severity") == "info" for f in found):
            status = "noted"
        else:
            status = "flagged"
        stages.append({"key": key, "status": status})
        return found

    def _skipped(key: str, reason: str, cause: str) -> list[dict]:
        """실행하지 않은 검사 단계를 이유와 함께 기록한다.

        이유를 남겨 통과와 구분한다. `reason`은 화면에 보이는 문장이고 `cause`는 집계용 코드다.
        문장으로 집계하면 문구를 고칠 때 집계가 끊긴다.
        """
        stages.append({"key": key, "status": "skipped", "reason": reason, "cause": cause})
        return []

    findings = [
        *_ran("caption", rules.check_caption(sentences)),
        # 메타데이터는 운영자가 영상에 입력한 값을 검사한다.
        # 라벨러 입력(`payload.meta`)은 읽지 않는다.
        # 영상을 찾지 못하면 검사할 값이 없으므로 `skipped`로 기록한다.
        *(_ran("metadata", rules.check_metadata(video_meta.of_video(video).model_dump()))
          if video is not None
          else _skipped("metadata", "영상을 찾을 수 없어 영상 메타를 확인하지 못했습니다",
                        "video_missing")),
        *_ran("pii", rules.check_pii(sentences)),
        # 설정 때문에 실행하지 않은 사유는 `settings`의 상수 문장을 쓴다. 화면이 그대로 보여 준다.
        *(_ran("profanity", rules.check_profanity(sentences, conf.profanity))
          if conf.profanity
          else _skipped("profanity", qc_settings.PROFANITY_EMPTY, "profanity_empty")),
        *(_ran("clip_length", rules.check_clip_length(duration, opts.min_seconds, opts.max_seconds))
          if duration is not None and (opts.min_seconds or opts.max_seconds)
          else _skipped("clip_length", qc_settings.CLIP_LENGTH_UNSET, "clip_length_unset")),
        # 비식별화 로그와 신고 내역을 함께 검사한다. GPU 없이 실행되는 부분이다.
        # 검사하지 않았으면 그 이유를 영상 여정과 같은 판정으로 기록한다(`_deident_skip`).
        *(_ran("deident", rules.check_deident(deident_report, deident_reported))
          if (deident_report or deident_reported)
          else _skipped("deident", *_deident_skip(video, project))),
    ]
    # 납품 관문 검사는 다시 구현하지 않고 그대로 호출한다. 두 곳에 구현하면 판정이 서로 달라진다.
    if video is not None:
        _meta, blockers = check_row(item, video, clip, item.submitted_at)
        findings += _ran("delivery", rules.from_delivery_blockers(blockers))
    else:
        _skipped("delivery", "영상을 찾을 수 없어 납품 규격을 확인하지 못했습니다", "video_missing")

    # 모델 검사. 규칙 판정이 이미 보류이면 결과가 바뀌지 않고 호출 비용만 들어서 모델을 건너뛴다.
    # 보류 여부는 판정(`verdict_of`)으로 확인한다.
    # 지적 유무로 보면 `info` 알림 하나로 모델 검사가 생략된다.
    model = prompt_hash = None
    adapter_key = "rules"
    if rules.verdict_of(findings) != "pass":
        for k in _MODEL_STAGES:
            _skipped(k, qc_settings.RULES_FLAGGED, "rules_flagged")
    else:
        res, cause = await _ask_model(db, item, conf, sentences)
        if res is None and cause == "model_failed" and not model_final:
            raise ModelRetry
        if res is None:
            # 모델을 설정하지 않은 경우(설정 문제)와 호출에 실패한 경우(장애)를 다른 사유로 남긴다.
            # 같은 사유로 남기면 운영자가 연결을 고칠지 새로 설정할지 판단할 수 없고
            # 장애도 셀 수 없다.
            reason = (qc_settings.MODEL_UNCONFIGURED if cause == "model_unconfigured"
                      else qc_settings.MODEL_FAILED)
            for k in _MODEL_STAGES:
                _skipped(k, reason, cause or "model_failed")
        else:
            findings += res.findings
            model, prompt_hash = res.model, res.prompt_hash
            adapter_key = conf.adapter_key
            hit = {f.get("check") for f in res.findings}
            for k in _MODEL_STAGES:
                # 용어집이 비어 있으면 검사할 용어가 없으므로 `skipped`로 기록한다.
                # 지적이 없다는 이유로 `passed`로 기록하면 검사하지 않은 단계가 통과로 보인다.
                if k == "glossary" and not conf.glossary:
                    _skipped(k, qc_settings.GLOSSARY_EMPTY, "glossary_empty")
                else:
                    stages.append({"key": k, "status": "flagged" if k in hit else "passed"})

    verdict = rules.verdict_of(findings)
    run_row = CaptionQcRun(
        work_item_id=item.work_item_id, trigger=trigger, verdict=verdict,
        findings=findings, adapter_key=adapter_key, model=model, prompt_hash=prompt_hash,
        stages=stages,
        # 검사 한 번에 걸린 벽시계 시간. 시간 대부분이 모델 호출 대기라서 CPU 시간 대신 잰다.
        # 규칙만 실행한 경우는 1초 미만이라 밀리초 단위로 둔다.
        elapsed_ms=int((time.monotonic() - _t0) * 1000),
    )
    db.add(run_row)

    # 게이트는 승인 직후와 다시 검사(승인본 검사)에서만 바꾼다.
    # 제출 시점 결과는 리뷰어에게 보여 주는 근거다. 제출 시점에 납품을 막으면 사람이 판정하기 전에
    # 자동 검사가 결론을 내게 된다.
    if trigger in CAPTION_QC_GATE_TRIGGERS:
        item.qc_status = rules.qc_status_of(verdict)
    audit.emit(
        db, event_type=f"caption_qc.{verdict}", target_kind="work_item",
        target_id=item.work_item_id, project_id=item.project_id,
        payload={"trigger": trigger, "adapter": adapter_key, "findings": len(findings),
                 "model": model},
    )
    return run_row


async def _ask_model(db, item, conf, sentences) -> tuple[adapters.QcResult | None, str | None]:
    """모델에 검사를 요청한다. 호출하지 못하면 결과는 `None`이다.

    호출부는 `None`을 받으면 `verdict`를 규칙 결과만으로 정한다. 단 `model_failed`이고 마지막
    시도가 아니면 기록하지 않고 다시 시도한다(`ModelRetry`).
    규칙 결과만으로 정한 실행은 `adapter_key`가 `rules`로 남아
    모델이 실행되지 않았다는 사실이 기록된다.

    Returns:
        `(결과, 사유 코드)`. 결과가 있으면 사유 코드는 `None`이다.
        결과가 없으면 사유 코드는 둘 중 하나다.
        `model_unconfigured`는 연결을 정하지 않은 설정 문제다.
        `model_failed`는 연결을 정했지만 호출에 실패한 장애다.
    """
    adapter = adapters.get(conf.adapter_key)
    if adapter is None or not conf.connection_name:
        return None, "model_unconfigured"
    conn = await db.scalar(
        select(Connection).where(
            Connection.org_id == (await db.get(Project, item.project_id)).org_id,
            Connection.name == conf.connection_name,
        )
    )
    if conn is None:
        # 설정된 이름의 연결이 조직에 없다. 삭제된 연결을 가리키는 설정이므로 장애로 센다.
        log.warning("캡션 QC 연결을 못 찾음: %s", conf.connection_name)
        return None, "model_failed"

    cfg_raw = conn.config or {}
    access = secret = ""
    if conn.secret_ciphertext:
        try:
            access, _, secret = crypto.decrypt(conn.secret_ciphertext).partition("\n")
        except ValueError:
            log.warning("캡션 QC 자격증명 복호 실패 connection=%s", conn.name)
            return None, "model_failed"
    cfg = adapters.ModelConfig(
        model=cfg_raw.get("model") or "", base_url=cfg_raw.get("base_url"),
        api_key=access, api_secret=secret, region=cfg_raw.get("region"),
    )
    try:
        res = await adapter.review(adapters.QcRequest(
            sentences=sentences, glossary=conf.glossary, prompts=conf.prompts,
            model_config=cfg,
        ))
    except adapters.QcNotConfiguredError as e:
        log.warning("캡션 QC 미설정: %s", e)
        return None, "model_failed"
    except adapters.QcUnparseableError as e:
        # 모델 응답을 해석하지 못한 경우는 지적 0건과 구분해 `model_failed`로 처리한다.
        # 호출부가 `adapter_key="rules"`로 기록해 모델이 판정에 참여하지 못했다는 사실이 남는다.
        log.warning("캡션 QC 모델 응답을 못 읽음 connection=%s: %s", conf.connection_name, e)
        return None, "model_failed"
    except Exception:  # noqa: BLE001 — 한 번 다시 부르고도 죽은 모델은 `rules`로 떨어진다
        # 어댑터는 일시 오류가 나면 한 번 다시 호출하고(`adapters._RETRY_ONCE_AFTER`),
        # 다시 실패하면 예외가 이 함수로 전달된다. 여기서 잡아야 `model_failed`로 기록된다.
        log.exception("캡션 QC 모델 호출 실패 connection=%s", conf.connection_name)
        return None, "model_failed"
    return res, None


def _sentences_of(payload: dict | None) -> list[dict]:
    """제출 payload에서 캡션 문장을 꺼낸다. 읽는 규칙은 `services.caption.sentences_of`에만 있다.

    이전 형식(`caption.brief` 문자열, `caption` 문자열)도 문장으로 나눠 검사한다.
    배열만 읽으면 화면에 캡션이 보이는 작업이 빈 캡션으로 보류된다.
    id는 위치로 붙인다(`s0`, `s1`, …).
    화면 `toSentences`도 같은 id를 붙여 지적이 같은 문장에 붙는다.
    """
    return caption.sentences_of(payload)


#: 비식별화 결과가 없는 원인별 `(화면 문장, 기계 코드)`.
#: 앞의 둘은 영상 여정과 같은 문장이다(`services.deident_gap`의 상수).
#: 코드 `deident_not_run`은 비식별화가 실행 중이거나 앞 단계를 기다릴 때만 쓴다.
#: 집계에서 "사용 안 함"과 "아직 안 끝남"을 구분하려고 원인마다 코드를 나눈다.
_DEIDENT_SKIP: dict[str, tuple[str, str]] = {
    "off": (DEIDENT_OFF, "deident_off"),
    "before_on": (DEIDENT_BEFORE_ON, "deident_before_on"),
    "waiting": ("비식별화가 아직 끝나지 않았습니다", "deident_not_run"),
    "running": ("비식별화가 아직 끝나지 않았습니다", "deident_not_run"),
    "failed": ("비식별화에 실패한 영상입니다", "deident_failed"),
}


def _deident_skip(video: Video | None, project: Project | None) -> tuple[str, str]:
    """비식별화 단계를 검사하지 않은 이유를 `(화면 문장, 기계 코드)`로 돌려준다.

    비식별화 결과도 신고도 없을 때 호출된다.
    판정은 영상 여정과 같은 곳(`services.deident_gap`)에서 한다.
    비식별화를 끈 프로젝트와 아직 실행되지 않은 영상을 다른 문장으로 표시한다.
    """
    if video is None:
        return "영상을 찾을 수 없어 비식별화를 확인하지 못했습니다", "video_missing"
    # 품질 검사 대상은 클립의 작업이라 분석이 이미 클립을 만든 영상이다(`downstream`).
    reached = reached_deident(
        probe_status=video.probe_status, transcode_status=video.transcode_status,
        publish_status=video.publish_status, downstream=True,
    )
    gap = deident_gap(
        video.deident_status, bool(project and project.deident_enabled), reached=reached)
    if gap is None:
        # `done`인데 결과 기록이 없는 경우. 완료 처리가 항상 기록을 남기므로 드물다.
        # 실행 전도 사용 안 함도 아니므로 결과 기록이 없다고 표시한다.
        return "비식별화 결과 기록이 없습니다", "deident_no_report"
    return _DEIDENT_SKIP[gap]


async def _was_deident_reported(db: AsyncSession, clip_id) -> bool:
    """클립에 비식별화 미흡 신고가 있었으면 True를 돌려준다.

    감사 로그를 근거로 쓴다. 신고로 바뀐 클립 상태(`rights_status`)는 관리자가 처리하면서
    다시 바뀌므로 신고가 있었다는 사실은 이벤트에만 남는다.
    """
    if clip_id is None:
        return False
    from api_gateway.models import DomainEvent

    hit = await db.scalar(
        select(DomainEvent.event_id)
        .where(
            DomainEvent.event_type == "clip.deident_miss_reported",
            DomainEvent.target_id == clip_id,
        )
        .limit(1)
    )
    return hit is not None
