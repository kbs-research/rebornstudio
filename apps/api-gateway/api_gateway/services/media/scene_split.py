"""장면 분할 잡의 DB 쪽 처리. 워커는 DB 없이 돈다.

자르는 규칙은 `analysis`에 있지만 결과물인 클립은 media의 자산이다
(Clip INSERT는 `media/clip.py`만 한다). 이 모듈은 presign·리스·단계 기록도 쓰는데
`analysis`는 DB에 붙지 않으므로(`analysis` README) media에 둔다.
`analysis`에서는 카탈로그와 등록부만 읽는다.

실행 모델은 `transcode`와 같은 틀이다.

    build_jobs(beat 1분) ─ 잡 페이로드 ─▶ q.transcode ─▶ 워커(무DB)
         │                                                   │
         └── pending→running · 토큰 · 리스               결과 ─┘
                                                              ▼
                                              complete(펜싱) ─▶ 클립 생성

워커는 입력 presigned GET 하나로 검출·병합하고 결과를 큐로 돌려보낸다.
클립 길이는 MI의 `maxSceneDurationSec`과 같은 프로젝트 설정에서 읽는다(`analysis/scene_options.py`).
낡은 토큰(회수·재투입 뒤 옛 워커가 다시 응답한 경우)의 보고는 버린다.

장면 분할은 원본 전체를 디코딩해 자원 사용 양상이 트랜스코딩과 같으므로 `q.transcode`를 쓴다.
큐를 나누면 CPU 예산이 둘로 나뉘어 어느 큐도 예산을 정확히 지키지 못한다.
`dispatch_publishes`도 같은 이유로 `q.transcode`를 쓴다.

리스는 변환과 같이 두 단계로 건다. 투입 때는 같은 큐의 긴 인코딩이 워커를 점유하는 동안
대기 잡이 회수되지 않도록 `transcode._QUEUE_WAIT_S`를 건다. 시작 하트비트가 리스를 `LEASE_S`로
줄이고 처리 중에는 주기마다 늘린다. 그래서 시작한 뒤 워커가 비정상 종료되면 몇 분 안에 회수된다.
워커의 실행 상한(`EXEC_CAP_S`)은 멈춘 ffmpeg를 끊는 값이고 리스와 따로 둔다.
"""
from __future__ import annotations

import logging
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import DateTime, cast, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import AnalysisJob, Clip, Project, User, Video
from api_gateway.services import audit, authz
from api_gateway.services.analysis import job_state, presets, scene_options
from api_gateway.services.joblock import lock_for_transaction
from api_gateway.services.media import clip as clip_service
from api_gateway.services.media import resolver, stage_runs, storage
from api_gateway.services.media import video as video_service
from api_gateway.services.media.transcode import (
    _BACKOFF_BASE_S,
    _BACKOFF_MAX_S,
    _NOT_STARTED_NOTE,
    _QUEUE_WAIT_S,
    _TRANSIENT_LIMIT,
    Revoke,
    _revoke,
)

log = logging.getLogger(__name__)

#: 장면 분할 어댑터 키. `analysis_job.adapter_key`에 이 값으로 적는다.
ADAPTER_KEY = "scenedetect"

#: 동시에 실행하는 잡 수의 상한. 실행 중인 수를 뺀 만큼만 새로 내보낸다.
#: 트랜스코딩과 같은 워커와 CPU를 쓰므로 인코딩 잡이 밀리지 않도록 작게 둔다.
MAX_INFLIGHT = 4

#: 시작한 잡의 리스 시간(초). 워커 하트비트(`HEARTBEAT_EVERY_S`)가 주기마다 이만큼 늘린다.
#: 하트비트를 한두 번 놓쳐도 회수되지 않도록 하트비트 주기의 몇 배로 둔다.
LEASE_S = 300

#: 워커가 하트비트를 보내는 간격(초). `LEASE_S`의 절반보다 짧아야 한다.
HEARTBEAT_EVERY_S = 60

#: 워커의 검출 실행 상한(초). 멈춘 ffmpeg에 워커가 묶이지 않게 끊는 값으로 리스와 별개다.
EXEC_CAP_S = 3600

#: 잡 URL의 TTL(초). 큐 대기 상한과 실행 상한의 합이다.
#: 검출은 원본을 처음부터 끝까지 스트리밍으로 읽으므로 늦게 시작한 잡도 끝까지 읽어야 한다.
#: 만료(403)는 워커가 일시 오류로 분류하고 다음 `build_jobs`가 URL을 새로 발급한다.
JOB_URL_TTL_S = _QUEUE_WAIT_S + EXEC_CAP_S

#: 시작 하트비트를 보낸 시도의 토큰을 담는 `payload` 키. 회수가 시작한 시도인지 가를 때 쓴다.
_STARTED_KEY = "started_token"
#: 연속 일시 오류 횟수의 `payload` 키. 성공하거나 영구 오류가 나면 지운다.
_STREAK_KEY = "transient_streak"
#: 다음 시도를 보낼 수 있는 시각(ISO)의 `payload` 키. 이 시각 전에는 투입하지 않는다.
_RETRY_AFTER_KEY = "retry_after"

#: 영구 오류 재시도 상한. 닿으면 `failed`로 확정한다.
MAX_ATTEMPTS = 3

#: 입력 영상 파일이 없어 잡을 내보내지 못한 경우의 사유. 준비 타임라인에 그대로 표시된다.
NO_FILE = "장면을 나눌 영상 파일이 없습니다."
#: 영상 길이 정보가 없어 구간을 만들 수 없는 경우의 사유.
NO_DURATION = "영상 길이를 알 수 없어 구간을 만들 수 없습니다."
# 선행 조건 실패 사유 목록. 조건이 갖춰지면 `build_jobs`가 이 사유로 실패한 잡을 다시 연다.
# 처리 오류의 사유는 워커가 보낸 문장이라 이 값과 겹치지 않는다.
_PRECONDITION_ERRORS = (NO_FILE, NO_DURATION)


def _stage_detail(basis: dict | None) -> dict:
    """단계 기록에 남길 분할 근거(적용한 규칙과 병합 전후의 구간 수)를 만든다.

    결과 수만 남기면 클립 수가 맞는지 나중에 확인할 수 없다.
    """
    if not basis:
        return {}
    return {"raw_count": basis.get("raw_count"), "merged_count": basis.get("merged_count"),
            "rule": basis.get("rule")}


def _ready_video_conds() -> list:
    """자를 수 있는 영상의 조건을 만든다.

    새 잡·대기 잡·선행 조건 실패 잡의 선택과 다시 실행(`retry`)이 함께 쓴다.
    준비 판정은 MI 분석 요청 게이트와 같은 `video._prep_unfinished_expr`를 쓰며
    규격 검증 통과와 영상 길이를 요구한다. 검증 전에 자르면 비식별화 전 원본에서 클립이 만들어진다.

    클립이 이미 있는 영상은 제외한다. 다시 자르면 같은 시간대에 클립이 중복으로 생겨
    라벨링이 진행된 클립과 새 클립 중 어느 쪽이 맞는지 가릴 수 없다.
    """
    keys = [p.key for p in presets.all_presets() if p.scene_adapter_key == ADAPTER_KEY]
    has_clip = select(Clip.clip_id).where(Clip.video_id == Video.video_id)
    owner = select(Project.project_id).where(
        Project.project_id == Video.project_id,
        Project.settings[presets.SETTINGS_KEY].astext.in_(keys),
    )
    return [
        owner.exists(),
        Video.deleted_at.is_(None),
        ~video_service._prep_unfinished_expr(),
        ~has_clip.exists(),
    ]


async def _eligible(db: AsyncSession, limit: int) -> list[Video]:
    """이번 주기에 자를 영상을 고른다. 잡이 아직 없고 준비가 끝난 영상이다."""
    if limit <= 0:
        return []
    has_job = select(AnalysisJob.id).where(
        AnalysisJob.video_id == Video.video_id, AnalysisJob.adapter_key == ADAPTER_KEY
    )
    return list((await db.scalars(
        select(Video)
        .where(*_ready_video_conds(), ~has_job.exists())
        .order_by(Video.created_at)
        .limit(limit)
    )).all())


def _without_retry_state(payload: dict | None) -> dict:
    """`payload`에서 일시 오류 연속 횟수와 재시도 시각을 뺀 사본을 돌려준다."""
    return {k: v for k, v in (payload or {}).items() if k not in (_STREAK_KEY, _RETRY_AFTER_KEY)}


def _reopen(job: AnalysisJob) -> None:
    """끝난 잡을 첫 시도 상태로 되돌려 다음 투입 주기에 다시 선택되게 한다.

    일시 오류 연속 기록도 지운다. 남기면 다시 연 잡이 일시 오류 한 번에 곧바로 멈출 수 있다.
    """
    job_state.set_status(job, "pending")
    job.attempt = 1
    job.error_msg = None
    job.token = None
    job.lease_until = None
    job.payload = _without_retry_state(job.payload)


async def _reopen_precondition_failures(db: AsyncSession, limit: int) -> list[AnalysisJob]:
    """선행 조건 실패로 끝난 잡 중 이제 자를 수 있는 영상의 잡을 다시 연다.

    길이나 파일이 없어 잡을 내보내지 못한 실패는 영상의 상태가 바뀌면 해소된다.
    다시 열지 않으면 그 영상은 잡이 이미 있어서 계속 대상에서 빠지고 클립이 생기지 않는다.
    처리 오류로 상한에 닿은 실패는 같은 입력이면 같은 오류가 나므로 운영자가 `retry`로 연다.
    """
    if limit <= 0:
        return []
    jobs = list((await db.scalars(
        select(AnalysisJob)
        .join(Video, Video.video_id == AnalysisJob.video_id)
        .where(
            AnalysisJob.adapter_key == ADAPTER_KEY,
            AnalysisJob.status == "failed",
            AnalysisJob.error_msg.in_(_PRECONDITION_ERRORS),
            *_ready_video_conds(),
        )
        .order_by(AnalysisJob.updated_at)
        .limit(limit)
        .with_for_update(skip_locked=True, of=AnalysisJob)
    )).all())
    for job in jobs:
        _reopen(job)
    if jobs:
        log.info("씬 분할 선행 조건 실패 %d건 다시 엶", len(jobs))
    return jobs


async def _claim(db: AsyncSession, job: AnalysisJob, video: Video, now: datetime,
                 opts: scene_options.SceneOptions) -> dict | None:
    """잡 행 하나를 워커로 내보낼 수 있게 준비한다. 상태·토큰·리스·입력 URL을 채운다.

    내보낼 수 없는 사유가 있으면 `failed`로 끝내고 잡을 내보내지 않는다.
    끝내지 않으면 매 주기 잡을 만들어 큐만 채우고 화면은 계속 "자르는 중"으로 표시한다.

    Returns:
        dict | None: 잡 페이로드. 내보내지 못했으면 `None`이고, 사유는 잡 행에 기록한다.
    """
    src = resolver.media_uri(video, "analysis_input")
    duration = float(video.duration_seconds or 0)
    if not src or duration <= 0:
        # 사유는 준비 타임라인에 그대로 표시된다.
        why = NO_FILE if not src else NO_DURATION
        job_state.set_status(job, "failed")
        job.error_msg = why
        job.token = None
        job.lease_until = None
        await stage_runs.start(db, video.video_id, "scene_split")
        await stage_runs.finish(db, video.video_id, "scene_split", ok=False, error=why)
        log.warning("씬 분할 불가 video=%s: %s", video.video_id, why)
        return None

    bucket, key = video_service._parse_s3_uri(src)
    token = uuid_mod.uuid4().hex
    job_state.set_status(job, "running")
    job.token = token
    # 큐 대기까지 덮는다. 워커의 시작 하트비트가 `LEASE_S`로 줄인다(`extend_lease`).
    job.lease_until = now + timedelta(seconds=_QUEUE_WAIT_S)
    await stage_runs.start(db, video.video_id, "scene_split")
    return {
        "video_id": str(video.video_id),
        "token": token,
        # 만료(403)는 워커가 일시 오류로 분류하고 다음 주기가 새 URL을 발급한다.
        "input_url": storage.presign_get_for(bucket=bucket, key=key,
                                             ttl_seconds=JOB_URL_TTL_S),
        "duration_seconds": duration,
        # 클립 길이는 MI의 `maxSceneDurationSec`과 같은 프로젝트 설정이다
        # (`analysis/scene_options.py`). 정하지 않았으면 워커 기본값을 쓴다.
        "max_seconds": opts.max_seconds,
        "heartbeat_every_s": HEARTBEAT_EVERY_S,
        "timeout_s": EXEC_CAP_S,
    }


async def build_jobs(db: AsyncSession) -> list[dict]:
    """자를 영상을 골라 DB 없는 워커용 잡 페이로드로 만든다. 1분 주기 beat가 부른다.

    잡은 이 순서로 고른다.
    1. 회수·재시도로 되돌아온 `pending` 행. 재시도 시각(`_RETRY_AFTER_KEY`)이 지나야 고른다.
    2. 선행 조건 실패로 멈춘 잡 중 이제 자를 수 있는 잡(다시 연다).
    3. 새 영상의 잡.
    `pending` 행도 영상의 준비가 끝났을 때만 고른다. 그래서 다시 실행한 잡이
    그사이 다시 열린 비식별화보다 먼저 원본을 자르지 않는다.

    빌더가 동시에 돌 수 있으므로 상한을 세기 전에 임계 구역을 잠근다(`lock_for_transaction`).
    잠그지 않으면 두 빌더가 같은 대기 잡을 함께 고르고 상한도 넘긴다.

    Returns:
        list[dict]: `queues.md`의 `scene_split_one` 잡 스키마.
    """
    await lock_for_transaction(db, "reborn:dispatch:scene_split")
    running = await db.scalar(
        select(func.count()).select_from(AnalysisJob).where(
            AnalysisJob.adapter_key == ADAPTER_KEY,
            AnalysisJob.status == "running",
            AnalysisJob.lease_until.is_not(None),
        )
    ) or 0
    limit = MAX_INFLIGHT - running
    if limit <= 0:
        return []

    now = datetime.now(UTC)
    retry_after = AnalysisJob.payload[_RETRY_AFTER_KEY].astext
    waiting = list((await db.scalars(
        select(AnalysisJob)
        .join(Video, Video.video_id == AnalysisJob.video_id)
        .where(
            AnalysisJob.adapter_key == ADAPTER_KEY, AnalysisJob.status == "pending",
            # 일시 오류 뒤 재시도를 기다리는 잡은 재시도 시각이 지나야 보낸다.
            or_(retry_after.is_(None), cast(retry_after, DateTime(timezone=True)) <= now),
            *_ready_video_conds(),
        )
        .order_by(AnalysisJob.updated_at).limit(limit)
    )).all())
    waiting += await _reopen_precondition_failures(db, limit - len(waiting))
    for video in await _eligible(db, limit - len(waiting)):
        job = AnalysisJob(
            project_id=video.project_id, video_id=video.video_id,
            adapter_key=ADAPTER_KEY, status="pending", attempt=1,
        )
        db.add(job)
        waiting.append(job)

    jobs: list[dict] = []
    projects: dict = {}
    for job in waiting:
        video = await db.get(Video, job.video_id)
        if video is None:
            continue
        if job.project_id not in projects:
            proj = await db.get(Project, job.project_id)
            projects[job.project_id] = scene_options.of_project(
                proj.settings if proj else None)
        payload = await _claim(db, job, video, now, projects[job.project_id])
        if payload is not None:
            jobs.append(payload)
    await db.commit()
    if jobs:
        log.info("씬 분할 %d건 투입", len(jobs))
    return jobs


async def _create_clips(db: AsyncSession, video: Video, segments: list[dict]) -> int:
    """구간 목록으로 클립을 만들고 클립마다 라벨링 작업을 연다. 생성은 `media.clip`을 거친다.

    초기 클립 상태 규칙은 `media.clip`에 있다.
    `create_scene_clip`이 savepoint로 중복을 흡수하므로 한 건의 실패가 배치 전체를
    실패시키지 않는다. 루프 안에서 rollback을 부르면 세션의 나머지 ORM 객체가 모두 만료된다.
    """
    made = 0
    for s in segments:
        ok = await clip_service.create_scene_clip(
            db, video=video,
            start_seconds=float(s["start"]), end_seconds=float(s["end"]),
            # 이 스택은 요약·캡션을 만들지 않으므로 빈 값으로 둔다.
            # 임의의 문자열을 넣으면 화면이 값이 있는 것으로 표시한다.
            auto_caption={}, speakers=[],
            scene_extra={"source": ADAPTER_KEY},
        )
        made += 1 if ok else 0
    # 클립마다 라벨링 작업을 연다. 클립은 만들 때부터 허가 상태라 바로 대기열에 들어간다.
    # 작업을 여는 조건(허가 상태인지)은 `work.service.ensure_for_clip` 한 곳이 판정한다.
    from api_gateway.services.work import service as work_service

    await db.flush()
    for c in (await db.scalars(select(Clip).where(Clip.video_id == video.video_id))).all():
        await work_service.ensure_for_clip(db, c)
    return made


async def extend_lease(db: AsyncSession, video_id: UUID, token: str) -> bool:
    """리스를 `LEASE_S`만큼 늘리고 시작한 시도의 토큰을 남긴다. 워커 하트비트 메시지가 부른다.

    첫 하트비트가 투입 때 건 긴 리스(큐 대기 포함)를 처리 시간 기준의 리스로 줄인다.
    시작 토큰(`_STARTED_KEY`)은 회수가 시작한 시도와 시작하지 못한 시도를 가르는 근거다.
    토큰이 다르면 아무것도 쓰지 않는다(펜싱).
    `payload`는 DB가 병합하므로(`payload || 패치`) 같은 행의 다른 키를 지우지 않는다.

    Returns:
        bool: 현재 시도의 하트비트라 리스를 늘렸으면 True.
    """
    patch = func.jsonb_build_object(_STARTED_KEY, token)
    row = (await db.execute(
        update(AnalysisJob)
        .where(AnalysisJob.video_id == video_id, AnalysisJob.adapter_key == ADAPTER_KEY,
               AnalysisJob.token == token, AnalysisJob.status == "running")
        .values(lease_until=datetime.now(UTC) + timedelta(seconds=LEASE_S),
                payload=func.coalesce(AnalysisJob.payload, text("'{}'::jsonb")).op("||")(patch))
        .returning(AnalysisJob.id)
    )).first()
    await db.commit()
    return row is not None


async def complete(
    db: AsyncSession,
    *,
    video_id: UUID,
    token: str,
    ok: bool,
    segments: list[dict] | None = None,
    basis: dict | None = None,
    error: str | None = None,
    transient: bool = False,
) -> str:
    """워커 회신을 판정한다. 판정은 이 함수에서만 한다(펜싱).

    낡은 토큰(회수·재투입 뒤 옛 워커가 다시 응답한 경우)의 보고는 버린다.
    받아들이면 다시 도는 시도를 덮어써 클립이 중복으로 생기거나 진행 중인 시도가 실패로 바뀐다.

    일시 오류는 시도를 소모하지 않고 연속 횟수에 따라 재시도 간격을 늘린다.
    연속 횟수가 `transcode._TRANSIENT_LIMIT`에 이르면 실패로 확정한다.
    상한 전의 영구 오류는 시도를 하나 올린다.

    Returns:
        str: `"done"`, `"failed"`, `"requeued"`(다시 대기로 돌림),
            `"stale"`(낡은 토큰이라 아무것도 하지 않음) 중 하나.
    """
    job = (await db.scalars(
        select(AnalysisJob).where(
            AnalysisJob.video_id == video_id, AnalysisJob.adapter_key == ADAPTER_KEY
        )
    )).first()
    if job is None or job.token != token:
        log.info("씬 분할 낡은 보고 버림 video=%s", video_id)
        return "stale"

    if ok:
        video = await db.get(Video, video_id)
        made = await _create_clips(db, video, segments or []) if video else 0
        # 클립이 생긴 영상의 영상 단위 분석 요청 티켓을 지운다(MI 회수·직접 추가와 같은 함수).
        # 티켓이 남으면 클립 시간에 잡히지 않는 작업 수에 들어가 진척 카드의 분모가 어긋난다.
        # 클립이 하나도 생기지 않았으면 티켓을 남긴다.
        from api_gateway.services.work import service as work_service

        await work_service.retire_video_ticket(db, video_id)
        job_state.set_status(job, "done")
        job.token = None
        job.lease_until = None
        job.payload = {**_without_retry_state(job.payload),
                       "basis": basis or {}, "clips_created": made}
        await stage_runs.finish(db, video_id, "scene_split", ok=True,
                                detail=_stage_detail(basis))
        await db.commit()
        log.info("씬 분할 완료 video=%s 클립 %d건", video_id, made)
        return "done"

    streak = int((job.payload or {}).get(_STREAK_KEY) or 0) + 1 if transient else 0
    if transient and streak < _TRANSIENT_LIMIT:
        # 일시 오류(429·타임아웃·만료된 presign)는 시도를 소모하지 않는다. 영구 실패로 세면
        # 오래 걸리는 영상일수록 실패로 끝난다. 연속 횟수에 따라 재시도 간격을 늘린다.
        delay = min(_BACKOFF_BASE_S * 2 ** (streak - 1), _BACKOFF_MAX_S)
        retry_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        job_state.set_status(job, "pending")
        job.token = None
        job.lease_until = None
        job.error_msg = error
        job.payload = {**(job.payload or {}), _STREAK_KEY: streak, _RETRY_AFTER_KEY: retry_at}
        await stage_runs.finish(db, video_id, "scene_split", ok=False,
                                error=f"{error} (재시도합니다)",
                                detail={"transient": True, "streak": streak,
                                        "retry_after": retry_at})
        await db.commit()
        return "requeued"
    if transient:
        # 연속 상한에 닿으면 다시 돌아도 해소되지 않는 일시 오류로 보고 실패로 확정한다.
        error = (f"{(error or '').rstrip('.')}. "
                 f"일시 오류가 {streak}회 이어져 자동 재시도를 멈췄습니다.")
    job.payload = _without_retry_state(job.payload)

    if not transient and job.attempt < MAX_ATTEMPTS:
        job.attempt += 1
        job_state.set_status(job, "pending")
        job.token = None
        job.lease_until = None
        job.error_msg = error
        await stage_runs.finish(db, video_id, "scene_split", ok=False, error=error)
        await db.commit()
        return "requeued"

    job_state.set_status(job, "failed")
    job.token = None
    job.lease_until = None
    job.error_msg = error
    await stage_runs.finish(db, video_id, "scene_split", ok=False, error=error)
    await db.commit()
    return "failed"


#: 회수 사유. 준비 타임라인의 시도 줄에 그대로 표시되므로 `리스`·`워커`·`회수` 같은
#: 내부 용어 없이 일어난 일만 쓴다.
_RECLAIM_NOTE = "처리 서버 응답이 끊겼습니다."


async def reclaim_expired(db: AsyncSession, *, revoke: Revoke | None = None) -> int:
    """리스가 끝난 `running` 행을 되살린다. 5분 주기 beat가 부른다.

    워커가 비정상 종료되면 그 행은 처리하는 워커 없이 계속 `running`으로 남는다.
    토큰을 지워 다시 응답한 옛 워커의 보고가 새 시도를 덮지 못하게 한다.

    시작 하트비트가 온 뒤 회수된 시도는 시도 한 번으로 센다. 디코더 충돌이나 메모리 초과처럼
    입력 자체가 워커를 비정상 종료시키는 영상은 세지 않으면 리스가 끝날 때마다 되살아나
    `q.transcode` 워커를 계속 점유한다. 시작 하트비트가 오지 않은 시도는 처리 서버가 잡을
    가져가지 못한 경우라서 세지 않는다(`transcode.reclaim_expired`와 같은 규칙).

    단계 이력도 `transient=True`로 닫는다(`stage_runs.finish`). 닫지 않으면 타임라인의
    그 시도 줄이 계속 "진행 중"으로 남는다. 상한에 닿은 마지막 회수만 실패로 확정한다.
    옛 토큰은 커밋한 뒤 `revoke`로 넘겨 워커가 큐에 남은 옛 메시지를 시작 전에 버리게 한다.

    Returns:
        int: 회수한 행 수(되살린 것과 상한에 닿아 실패로 확정한 것의 합).
    """
    now = datetime.now(UTC)
    expired = list((await db.scalars(
        select(AnalysisJob).where(
            AnalysisJob.adapter_key == ADAPTER_KEY,
            AnalysisJob.status == "running",
            AnalysisJob.lease_until.is_not(None),
            AnalysisJob.lease_until < now,
        ).with_for_update(skip_locked=True)
    )).all())
    settled = 0
    old_tokens = [job.token for job in expired if job.token]
    for job in expired:
        started = (job.payload or {}).get(_STARTED_KEY) == job.token
        job.token = None
        job.lease_until = None
        if not started:
            job_state.set_status(job, "pending")
            job.error_msg = _NOT_STARTED_NOTE
            await stage_runs.finish(db, job.video_id, "scene_split", ok=False,
                                    error=_NOT_STARTED_NOTE,
                                    detail={"transient": True, "reclaimed": True})
            continue
        if job.attempt < MAX_ATTEMPTS:
            job.attempt += 1
            job_state.set_status(job, "pending")
            job.error_msg = _RECLAIM_NOTE
            await stage_runs.finish(db, job.video_id, "scene_split", ok=False,
                                    error=f"{_RECLAIM_NOTE} 잠시 후 다시 시도합니다.",
                                    detail={"transient": True})
            continue
        # 상한에 닿으면 워커를 비정상 종료시키는 입력으로 보고 실패로 확정한다.
        job_state.set_status(job, "failed")
        job.error_msg = f"{_RECLAIM_NOTE} 다시 시도할 수 있는 횟수를 넘어 중단했습니다."
        await stage_runs.finish(db, job.video_id, "scene_split", ok=False,
                                error=job.error_msg)
        settled += 1
    await db.commit()
    _revoke(revoke, old_tokens)
    if expired:
        log.warning("씬 분할 리스 만료 회수 %d건 (상한 도달로 실패 확정 %d건)",
                    len(expired), settled)
    return len(expired)


async def retry(db: AsyncSession, project_id: UUID, video_id: UUID, user: User) -> None:
    """실패한 장면 분할을 다시 연다. project_admin이 부른다.

    처리 오류로 상한에 닿은 실패는 자동으로 다시 열지 않으므로 운영자가 이 경로로 연다.
    선행 조건 실패도 자동으로 다시 열리기 전에 먼저 다시 실행할 수 있다.
    시도 횟수를 1로 되돌린다. 되돌리지 않으면 다음 실패 한 번에 곧바로 다시 `failed`가 된다.

    클립이 있으면 거절한다. 다시 자르면 같은 시간대에 클립이 중복으로 생긴다.
    자를 준비가 끝나지 않은 영상도 거절한다. 다음 투입 주기가 그 잡을 고르지 않아
    화면에 실패가 그대로 남는다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            클립이 이미 있는 경우(409 `scene_split_has_clips`),
            실패한 장면 분할이 없는 경우(409 `scene_split_not_failed`),
            자를 준비가 끝나지 않은 경우(409 `scene_split_not_ready`).
    """
    video = await db.get(Video, video_id)
    if not video or video.project_id != project_id or video.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_project_admin(db, project_id, user)
    if await db.scalar(select(Clip.clip_id).where(Clip.video_id == video_id).limit(1)):
        raise HTTPException(status.HTTP_409_CONFLICT, "scene_split_has_clips")
    job = await db.scalar(
        select(AnalysisJob).where(
            AnalysisJob.video_id == video_id, AnalysisJob.adapter_key == ADAPTER_KEY
        ).with_for_update()
    )
    if job is None or job.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "scene_split_not_failed")
    ready = await db.scalar(
        select(Video.video_id).where(Video.video_id == video_id, *_ready_video_conds())
    )
    if ready is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "scene_split_not_ready")

    before = (job.error_msg or "")[:200]
    attempts = job.attempt
    _reopen(job)
    audit.emit(
        db, event_type="video.scene_split_retried", target_kind="video", target_id=video_id,
        actor_user_id=user.user_id, project_id=project_id,
        payload={"previous_error": before, "previous_attempt": attempts},
    )
    await db.commit()
