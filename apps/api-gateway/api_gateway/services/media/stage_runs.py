"""준비 단계 이력의 기록과 조회.

기록 규칙: 상태를 전이하는 코드가 한 줄로 부른다. 커밋은 호출자의 트랜잭션이 한다.
  start(...): 단계 시작을 기록하고 running 행을 추가한다. attempt는 직전 최대에 1을 더한다.
              앞서 열린 running 행은 버려진 시도로 닫는다.
  finish(...): 최신 running 행을 ok나 failed로 닫는다.
               running 행이 없으면 닫힌 행을 새로 넣는다.
               기록이 빠진 경우(이력 행이 없는 영상, 경합)는
               관측 손실로 보고 오류로 처리하지 않는다.

조회: 비식별화 리스 회수와 늦은 결과 채택(`open_started_at`), 건너뛰기 감사 기록의
시도 수(`attempt_counts`), 준비 타임라인의 단계별 최신 상태(`latest_runs`)가 읽는다.
타임라인 조립(`video.prep_timeline`)은 이력 행이 없는 단계를 현재 상태 열에서 파생한다.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import Integer, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import MediaStageRun
from api_gateway.services import error_text

log = logging.getLogger(__name__)

Stage = Literal["upload", "probe", "transcode", "deident", "publish", "register",
                "scene_split", "analyze", "summarize"]

# 준비 단계 순서. 타임라인의 행 순서이며 prep_stage의 기계 단계 순서와 같다.
# `scene_split`은 모든 스택에 있고 분할 주체만 스택마다 다르다.
# MI 스택은 MI가 분할까지 하므로 이 단계를 건너뜀과 사유로 표시한다(`video.prep_timeline`).
# 샷 검출·고정 길이·사람의 TC 입력 스택에서는 이 단계가 실제로 실행된다.
# 분할을 별도 단계로 두어 스택마다 파이프라인을 따로 만들지 않는다.
STAGE_ORDER: tuple[Stage, ...] = (
    "upload", "probe", "transcode", "deident", "publish", "register",
    "scene_split", "analyze", "summarize",
)


async def start(
    db: AsyncSession, video_id: UUID, stage: Stage, *, detail: dict | None = None,
) -> None:
    """단계 시작을 기록하고 running 행을 추가한다.

    attempt는 같은 (영상, 단계)의 직전 최대 attempt에 1을 더한 값이다.
    앞서 열린 running 행은 먼저 닫는다.
    """
    prev = await db.scalar(
        select(func.coalesce(func.max(MediaStageRun.attempt), 0)).where(
            MediaStageRun.video_id == video_id, MediaStageRun.stage == stage
        )
    )
    # 새 시도를 시작하기 전에 앞선 running 행을 버려진 시도(failed)로 닫는다.
    # 리스 만료로 회수돼 재투입되면 앞 시도의 행이 열린 채 남고,
    # `finish`는 최신 running 행 하나만 닫는다.
    # 여기서 닫지 않으면 타임라인이 끝난 시도를 진행 중으로 표시하고,
    # 리스 회수(`open_started_at`)가 버려진 시도의 시작 시각을 읽는다.
    now = datetime.now(UTC)
    await db.execute(
        update(MediaStageRun)
        .where(MediaStageRun.video_id == video_id, MediaStageRun.stage == stage,
               MediaStageRun.status == "running")
        .values(status="failed", finished_at=now, error="새 시도가 시작되어 이 시도는 버려졌습니다")
    )
    db.add(MediaStageRun(
        video_id=video_id, stage=stage, attempt=int(prev or 0) + 1,
        status="running", started_at=now, detail=detail or {},
    ))


def _screen_text(error: object) -> str | None:
    """실패 사유를 저장할 화면 문구로 바꾼다. 준비 타임라인의 실패 줄이 이 값을 표시한다.

    사유를 넘기는 곳은 단계마다 흩어져 있어(규격 검사·변환·비식별화·분석·장면 분할·재생본)
    화면 문구 변환은 이 함수 한 곳에서 한다.
    호출부마다 변환하면 새 단계에서 변환이 빠져 ffprobe 명령 실패, presigned URL 끝부분,
    파이썬 예외 타입이 화면에 그대로 나간다.
    `error_text`는 한글이 있는 사유를 그대로 통과시키므로 운영자용 문구로 쓴 사유는 바뀌지 않는다.
    원문은 각 호출부의 감사 이벤트에 남는다.
    """
    said = error_text.operator_text(error)
    return said[:1000] if said else None


async def finish(
    db: AsyncSession, video_id: UUID, stage: Stage, *,
    ok: bool, error: str | None = None, detail: dict | None = None,
) -> None:
    """단계 종료를 기록하고 최신 running 행을 닫는다.

    running 행이 없으면 닫힌 행을 새로 넣는다. 이때 생기는 관측 손실은 허용한다.
    """
    now = datetime.now(UTC)
    status = "ok" if ok else "failed"
    latest_running = (
        select(MediaStageRun.id)
        .where(
            MediaStageRun.video_id == video_id,
            MediaStageRun.stage == stage,
            MediaStageRun.status == "running",
        )
        .order_by(MediaStageRun.id.desc()).limit(1).scalar_subquery()
    )
    row = (await db.execute(
        update(MediaStageRun)
        .where(MediaStageRun.id == latest_running)
        .values(
            status=status, finished_at=now,
            error=_screen_text(error),
            **({"detail": detail} if detail else {}),
        )
        .returning(MediaStageRun.id)
    )).first()
    if row is None:
        # 닫을 running 행이 없다.
        # 기록이 빠졌거나(이력 행이 없는 영상, 경합) running 행이 누출된 경우다.
        # attempt는 `start`와 같은 규칙으로 직전 최대에 1을 더한다.
        # 1로 고정하면 같은 번호가 두 번 생겨 시도 수(`attempt_counts`)가 실제보다 적게 나온다.
        prev = await db.scalar(
            select(func.coalesce(func.max(MediaStageRun.attempt), 0)).where(
                MediaStageRun.video_id == video_id, MediaStageRun.stage == stage
            )
        )
        log.warning(
            "stage_runs.finish: 닫을 running 행 없음 — video=%s stage=%s (누락 또는 누출)",
            video_id, stage,
        )
        db.add(MediaStageRun(
            video_id=video_id, stage=stage, attempt=int(prev or 0) + 1, status=status,
            finished_at=now, error=_screen_text(error),
            detail=detail or {},
        ))


async def open_started_at(db: AsyncSession, video_id: UUID, stage: Stage) -> datetime | None:
    """열려 있는 최신 시도의 시작 시각을 돌려준다. 열린 시도가 없으면 `None`이다.

    리스 회수가 그 시도를 닫기 전에 부른다. 닫은 뒤에는 어느 행이 회수된 시도인지 알 수 없다.
    """
    return await db.scalar(
        select(MediaStageRun.started_at)
        .where(MediaStageRun.video_id == video_id, MediaStageRun.stage == stage,
               MediaStageRun.status == "running")
        .order_by(MediaStageRun.id.desc()).limit(1)
    )


async def latest_runs(db: AsyncSession, video_id: UUID) -> dict[str, MediaStageRun]:
    """영상의 단계별 최신 시도를 1건씩 돌려준다. 준비 타임라인이 각 단계의 상태로 쓴다."""
    sub = (
        select(
            MediaStageRun,
            func.row_number().over(
                partition_by=MediaStageRun.stage, order_by=MediaStageRun.id.desc()
            ).label("rn"),
        )
        .where(MediaStageRun.video_id == video_id)
        .subquery()
    )
    aliased = MediaStageRun.__table__.alias()  # noqa: F841 — subquery 매핑 대신 직접 선택
    rows = (await db.execute(
        select(sub).where(sub.c.rn == 1)
    )).all()
    out: dict[str, MediaStageRun] = {}
    for r in rows:
        run = MediaStageRun(
            video_id=r.video_id, stage=r.stage, attempt=r.attempt, status=r.status,
            started_at=r.started_at, finished_at=r.finished_at, error=r.error,
            detail=r.detail or {},
        )
        run.id = r.id
        out[r.stage] = run
    return out


async def attempt_counts(db: AsyncSession, video_id: UUID) -> dict[str, int]:
    """단계별 총 시도 수를 돌려준다. 비식별화 건너뛰기 감사 기록이 실제로 보낸 횟수로 싣는다."""
    rows = (await db.execute(
        select(MediaStageRun.stage, func.max(MediaStageRun.attempt).cast(Integer))
        .where(MediaStageRun.video_id == video_id)
        .group_by(MediaStageRun.stage)
    )).all()
    return {stage: int(n or 0) for stage, n in rows}

