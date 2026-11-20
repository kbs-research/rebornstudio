"""클립 씬 썸네일을 자동으로 만드는 워커 진입점.

MAIU가 클립에 주는 `scene_thumbnail_url`은 1시간 TTL로 만료된다.
MAIU 썸네일 버킷은 403을 돌려주어 다시 서명할 수도 없다.
그래서 클립이 가리키는 영상에서 ffmpeg로 클립 구간의 프레임을 추출한다.
비식별화를 쓰는 영상은 비식별화본이 확정된 뒤 비식별화본에서 추출한다(`thumbnail_source`).
추출한 프레임은 영상의 저장소 버킷(`clip-thumbs/{clip_id}.jpg`)에 저장한다.
키는 `clips.metadata.scene_thumbnail_key`에, 버킷은 `scene_thumbnail_bucket`에 기록한다.
`clip._thumbnail_url`이 응답마다 이 키를 새로 서명하므로 URL이 만료되지 않는다.

beat 태스크 `run_pending_clip_thumbnails`(worker-io, 1분 주기)가 부른다.
수동 일괄 처리는 `scripts/ops/backfill_clip_thumbnails.py`가 `backfill`로 한다.
두 경로 모두 `_process_one`을 써서 원천 선택·출처 기록·실패 정책이 같다.

처리량은 한 주기에 `min(BATCH_LIMIT, TIME_BUDGET_S / t)`건이다. t는 클립 한 건의 처리 시간이다.
클립마다 presigned URL에서 range-seek로 프레임 하나만 받는다.
t가 약 0.6초면 한 주기에 40건을 처리하고, N건을 다시 만드는 데 `ceil(N / 40)`분이 걸린다.
망이 느려 t가 1초를 넘으면 시간 예산에 먼저 닿는다.
같은 큐(`q.media`)의 규격 검사와 포스터 작업이 밀리지 않도록 한 번에 하나만 실행한다
(`single_runner`). 상한 근거는 `queues.md`의 q.media 행에 있다.

영구 실패(주소 해석·프레임 추출·업로드)는 `thumbnail_retry_count`를 올리고
다음 주기에 다시 시도한다.
`_MAX_RETRIES`에 닿으면 `thumbnail_failed`를 `"true"`로 확정해 `list_pending`에서 뺀다(dead-letter).
일시 오류(망·스로틀·URL 만료)는 재시도 횟수에 세지 않고 지수 백오프 뒤에 다시 시도한다.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar
from uuid import UUID

from reborn_transcode.input_guard import guard_args
from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Clip, Video
from api_gateway.services.joblock import single_runner
from api_gateway.services.media import clip as clip_service
from api_gateway.services.media import storage, thumbnail_source
from api_gateway.services.media.video import _parse_s3_uri
from api_gateway.services.transient import is_transient_boto, is_transient_ffmpeg

log = logging.getLogger(__name__)

# 썸네일을 올리는 버킷 프리픽스와 `extra` 키
_THUMB_PREFIX = "clip-thumbs"
_KEY = "scene_thumbnail_key"
#: 썸네일을 올린 버킷의 `extra` 키.
BUCKET_KEY = "scene_thumbnail_bucket"
#: 썸네일을 추출한 파일의 종류(`thumbnail_source.MASKED`·`SOURCE`)를 담는 `extra` 키.
#: 이 키가 없는 행은 원본에서 추출한 썸네일로 본다.
_FROM_KEY = "scene_thumbnail_from"
_ERROR_KEY = "thumbnail_error"
_RETRY_KEY = "thumbnail_retry_count"
_FAILED_KEY = "thumbnail_failed"
#: 확정된 실패 표식의 키.
#: 재생성(`video_thumbnail.retry_failed`)과 DTO(`clip._to_dto`)가 읽는다.
FAILED_KEY = _FAILED_KEY
#: 실패 사유의 키. DTO가 화면 문구로 바꿔 싣는다.
ERROR_KEY = _ERROR_KEY
_MAX_RETRIES = 3
_SCALE_WIDTH = 480  # 썸네일 폭(px). 그리드 카드에 표시할 크기라 원본 해상도까지 쓰지 않는다

#: 한 주기에 가져오는 클립 수의 상한. 가져온 클립은 처리가 끝날 때까지 행 잠금을 유지한다.
BATCH_LIMIT = 40
#: 한 주기의 처리 시간 예산(초). 넘으면 남은 클립은 다음 주기로 미룬다.
#: q.media 워커는 동시에 2개를 처리하고 나머지 한 슬롯은 규격 검사와 포스터가 쓴다.
#: 포스터 예산(`video_thumbnail.TIME_BUDGET_S`)과 합쳐도 beat 주기(60초)를 넘지 않는다.
TIME_BUDGET_S = 40.0
#: 동시 실행을 막는 advisory lock 키('RBTC'). 락 규약은 `services/joblock.py`에 있다.
LOCK_KEY = 0x52425443

_T = TypeVar("_T")

# 일시 오류는 재시도 횟수에 세지 않는 대신 지수 백오프 뒤에 다시 시도한다.
# 바로 다시 시도하면 망이 끊긴 동안 같은 클립이 매 주기 후보 상한을 차지해 나머지 클립이 밀린다.
_STREAK_KEY = "thumbnail_transient_streak"
_RETRY_AFTER_KEY = "thumbnail_retry_after"

#: 클립 구간이 바뀌면 지우는 키. 썸네일은 구간 안의 한 프레임이라 구간이 바뀌면 다시 만든다.
#: 키를 지우면 `list_pending`이 다음 주기에 다시 가져온다.
#: 이전 구간에서 실패한 클립이 새 구간에서는 성공할 수 있어 실패 기록도 함께 지운다.
RESET_KEYS: tuple[str, ...] = (
    _KEY, _FROM_KEY, _ERROR_KEY, _RETRY_KEY, _FAILED_KEY, _STREAK_KEY, _RETRY_AFTER_KEY,
)
_BASE_BACKOFF_S = 60
_MAX_BACKOFF_S = 3600


class BusyError(RuntimeError):
    """다른 실행이 락을 점유해 일괄 생성을 시작하지 못했을 때의 예외."""


class FrameError(RuntimeError):
    """프레임 추출 실패 예외. `transient`가 참이면 재시도 상한을 소모하지 않는다.

    실패를 `None` 하나로 돌려주면 망 순단과 깨진 파일을 구분하지 못한다.
    그러면 망 순단도 재시도 횟수를 늘려 세 번째 실패에서 `*_failed`로 확정된다.
    """

    def __init__(self, message: str, *, transient: bool) -> None:
        """실패 메시지와 일시 오류 여부를 받는다."""
        super().__init__(message)
        self.transient = transient


def _extract_frame(video_url: str, ts: float) -> bytes:
    """presigned 영상 URL에서 `ts`초 위치의 프레임 1장을 JPEG bytes로 돌려준다.

    `-ss`를 `-i` 앞에 두어 입력 seek(HTTP range)로 필요한 바이트만 받는다.
    영상 전체를 내려받지 않는 대신 망 상태가 실패 원인이 되므로 일시 오류와 영구 실패를 구분한다.
    입력 앞에 프로토콜·디먹서 제한(`reborn_transcode.input_guard`)을 두어 재생목록으로 읽히는
    파일이 워커 안에서 다른 주소를 열지 않게 한다.

    Raises:
        FrameError: 추출에 실패한 경우.
            `transient`가 참이면 일시 오류(망·스로틀·URL 만료)라 재시도 상한을 소모하지 않는다.
    """
    out = tempfile.mktemp(suffix=".jpg")
    try:
        # 프레임 하나라 스레드를 늘려도 빨라지지 않아 디코더·인코더·필터를 한 스레드로 둔다.
        # 같은 워커의 다른 슬롯과 짧은 작업(probe·회수)이 코어를 나눠 쓴다.
        r = subprocess.run(
            ["ffmpeg", "-y", "-threads", "1", "-ss", f"{ts:.3f}", *guard_args(video_url),
             "-i", video_url, "-frames:v", "1", "-vf", f"scale={_SCALE_WIDTH}:-1", "-q:v", "4",
             "-threads", "1", "-filter_threads", "1", out],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90,
        )
        if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
            err = (r.stderr or b"").decode("utf-8", "replace")
            raise FrameError(_last_ffmpeg_line(err) or "프레임을 추출하지 못했습니다",
                             transient=is_transient_ffmpeg(err))
        return Path(out).read_bytes()
    except subprocess.TimeoutExpired as e:
        # 90초를 넘기면 파일 손상이 아닌 느린 망으로 보고 일시 오류로 처리한다.
        raise FrameError("프레임 추출이 90초를 넘겨 중단했습니다", transient=True) from e
    except OSError as e:
        # 사유(`thumbnail_error`)는 화면에 표시되므로 원문(경로·errno)은 로그에만 남긴다.
        # `thumbnail_reason`은 한글이 든 문장을 그대로 두므로 원문을 붙이면 화면에 표시된다.
        log.warning("ffmpeg 실행 실패: %s", e)
        raise FrameError("프레임 추출 도구를 실행하지 못했습니다", transient=True) from e
    finally:
        if os.path.exists(out):
            os.unlink(out)


#: 주소의 쿼리스트링. ffmpeg는 실패하면 입력 URL을 stderr에 그대로 찍고, presigned URL의
#: 쿼리에는 접근 키와 서명이 있다(`reborn_transcode.probe`·`transcode`와 같은 규칙).
_QUERY_RE = re.compile(r"\?[^\s'\"]*")


def _last_ffmpeg_line(stderr: str) -> str:
    """ffmpeg stderr에서 버전 배너·빌드 옵션을 뺀 마지막 줄을 뽑는다.

    뽑은 줄은 실패 사유(`thumbnail_error`)로 DB에 저장되고, 화면에는 `clip.thumbnail_reason`이
    바꾼 문구가 표시된다.
    주소의 쿼리스트링은 `?…`로 지운다. 서명이 DB에 남으면 유효 기간 동안 그 줄을 읽은 사람이
    객체를 읽을 수 있다.
    """
    for line in reversed((stderr or "").strip().splitlines()):
        t = line.strip()
        if t and not t.startswith(("ffmpeg version", "  built with", "  configuration:",
                                   "  lib", "Input #", "Stream #", "Metadata:")):
            return _QUERY_RE.sub("?…", t)[:200]
    return ""


def pending_select() -> Select[tuple[Clip, Video]]:
    """썸네일 후보의 조건만 담은 select를 만든다.

    상한·정렬·행 잠금은 `list_pending`이 더한다. 조건과 상한을 나눠 두어, 다음 주기 후보인지
    확인할 때 배치 상한 때문에 후보가 아니라고 잘못 판정하지 않게 한다.
    """
    return (
        select(Clip, Video)
        .join(Video, Video.video_id == Clip.video_id)
        .where(
            ~func.jsonb_exists(Clip.extra, _KEY),
            Clip.extra[_FAILED_KEY].astext.is_distinct_from("true"),
            # 일시 오류로 백오프 중인 클립은 뺀다. 포함하면 막힌 클립이 매 주기 상한을 차지한다.
            or_(
                Clip.extra[_RETRY_AFTER_KEY].astext.is_(None),
                Clip.extra[_RETRY_AFTER_KEY].astext <= datetime.now(UTC).isoformat(),
            ),
            # 비식별화본에서 추출하는 영상은 원본(`source_uri`)이 없어도 후보다.
            or_(Video.source_uri.isnot(None), Video.deident_status == "done"),
            Video.deleted_at.is_(None),
            # 비식별화를 쓰는 영상은 비식별화본이 확정될 때까지 기다린다(`thumbnail_source`).
            thumbnail_source.ready_expr(),
        )
    )


async def invalidate_unmasked(db: AsyncSession) -> int:
    """비식별화를 쓰는 영상의 클립 중 원본에서 추출한 썸네일의 키를 지우고 지운 수를 돌려준다.

    키를 지운 클립은 다시 후보가 되어 비식별화본이 확정된 뒤 다시 추출된다.
    그 사이에는 비식별화하지 않은 프레임을 표시하지 않도록 썸네일을 비워 둔다.
    키를 지운 행은 조건에 맞지 않으므로 매 주기 불러도 된다(멱등).
    저장소의 이전 이미지는 다시 추출할 때 같은 키에 덮어쓴다.
    """
    expr = Clip.extra.op("-")(_KEY).op("-")(BUCKET_KEY).op("-")(_FROM_KEY)
    rows = (await db.execute(
        update(Clip)
        .where(
            Clip.video_id == Video.video_id,
            func.jsonb_exists(Clip.extra, _KEY),
            thumbnail_source.stale_expr(Clip.extra[_FROM_KEY].astext),
        )
        .values(extra=expr)
        .returning(Clip.clip_id)
        .execution_options(synchronize_session=False)
    )).all()
    if rows:
        log.warning("clip_thumbnail 원본에서 뽑은 썸네일 %d건을 다시 만든다", len(rows))
    return len(rows)


def fair_select(base: Select[tuple[Clip, Video]]) -> Select[tuple[Clip, Video]]:
    """후보를 프로젝트별로 번갈아 가져오는 순서로 정렬한 select를 만든다.

    프로젝트 안에서는 오래된 클립부터 순번(`turn`)을 매긴다. 순번이 같으면 오래된 클립을 앞에 둔다.
    오래된 순서로만 가져오면 클립이 수천 개인 프로젝트(대량 반입, 비식별화본으로 다시 추출)가
    끝날 때까지 다른 프로젝트의 새 클립이 썸네일 없이 기다린다.
    윈도 함수와 행 잠금(FOR UPDATE)은 한 SELECT에 같이 쓸 수 없어 순번은 서브쿼리에서 매긴다.

    Args:
        base: 후보 조건을 담은 select(`pending_select`). 테스트는 여기에 조건을 더해 범위를 좁힌다.
    """
    turn = (
        base.with_only_columns(
            Clip.clip_id,
            func.row_number().over(
                partition_by=Clip.project_id, order_by=(Clip.created_at, Clip.clip_id),
            ).label("turn"),
        )
        .subquery()
    )
    return (
        select(Clip, Video)
        .join(Video, Video.video_id == Clip.video_id)
        .join(turn, turn.c.clip_id == Clip.clip_id)
        .order_by(turn.c.turn, Clip.created_at, Clip.clip_id)
    )


async def list_pending(db: AsyncSession, limit: int = BATCH_LIMIT) -> list[tuple[Clip, Video]]:
    """썸네일이 없고 추출할 영상이 있는 클립을 프로젝트별로 번갈아 고른다(`fair_select`).

    FOR UPDATE SKIP LOCKED로 행을 잠근다. 후보 조건은 `pending_select`에 있다.
    """
    rows = await db.execute(
        fair_select(pending_select())
        .limit(limit)
        .with_for_update(skip_locked=True, of=Clip)
    )
    return list(rows.all())


async def run_within_budget(
    items: Sequence[_T],
    handle: Callable[[_T], Awaitable[bool]],
    *,
    budget_s: float,
    name: str,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """항목을 차례로 처리하다 시간 예산을 넘기면 멈추고 성공 수를 돌려준다.

    예산은 다음 항목을 시작하기 전에 확인하고, 시작한 항목은 끝까지 처리한다.
    그래서 실제 소요 시간은 예산에 한 항목의 상한(ffmpeg 90초)을 더한 값까지 늘 수 있다.
    그때 겹치는 다음 주기의 실행은 락이 막는다.
    한 건의 예외는 로그로 남기고 다음 항목으로 넘어간다. 예외를 올리면 막힌 한 건이
    매 주기 같은 위치에서 배치를 끊는다.

    Args:
        items: 처리할 항목.
        handle: 항목 하나를 처리하고 성공하면 True를 돌려주는 함수.
        budget_s: 시간 예산(초).
        name: 로그에 쓰는 작업 이름.
        clock: 경과 시간을 재는 시계. 테스트에서 바꿔 넣는다.
    """
    started = clock()
    done = 0
    for i, item in enumerate(items):
        if clock() - started >= budget_s:
            log.info("%s 시간 예산 %.0f초를 넘겨 %d건을 다음 주기로 미룸",
                     name, budget_s, len(items) - i)
            break
        try:
            if await handle(item):
                done += 1
        except Exception:  # noqa: BLE001
            log.exception("%s 처리 실패 (%d번째 항목)", name, i)
    return done


async def _mark_failure(
    db: AsyncSession, clip: Clip, msg: str, *, transient: bool = False
) -> None:
    """썸네일 실패를 기록한다. 일시 오류는 재시도 상한을 소모하지 않는다.

    일시 오류를 깨진 파일과 같이 세면 망이 잠깐 불안정했던 영상이 썸네일을 끝내 얻지 못한다.

    카운터는 클립을 가져온 시점의 `clip.extra` 값에서 계산한다. 그 뒤 ffmpeg가 수십 초 동안 돌므로
    다른 워커가 쓴 키를 덮지 않도록 `extra` 전체를 되쓰지 않고 DB에서 병합한다.
    """
    if transient:
        streak = int((clip.extra or {}).get(_STREAK_KEY, 0)) + 1
        delay = min(_BASE_BACKOFF_S * (2 ** (streak - 1)), _MAX_BACKOFF_S)
        await clip_service.merge_extra(db, clip.clip_id, {
            _ERROR_KEY: f"[일시] {msg}"[:200],
            _STREAK_KEY: streak,
            _RETRY_AFTER_KEY: (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(),
        })
        log.info("clip_thumbnail 일시 오류 clip=%s streak=%d %ds 뒤 재시도",
                 clip.clip_id, streak, delay)
        return

    count = int((clip.extra or {}).get(_RETRY_KEY, 0)) + 1
    patch: dict = {_ERROR_KEY: msg[:200], _RETRY_KEY: count}
    if count >= _MAX_RETRIES:
        patch[_FAILED_KEY] = "true"
        log.error("clip_thumbnail_dead_lettered clip=%s after %d attempts", clip.clip_id, count)
    # 영구 실패가 나면 일시 오류 기록(연속 횟수·재시도 시각)을 지운다.
    # 남기면 다음 일시 오류의 백오프가 이전 연속 횟수에 이어서 계산된다.
    await clip_service.merge_extra(db, clip.clip_id, patch,
                                   remove=(_STREAK_KEY, _RETRY_AFTER_KEY))


async def _process_one(db: AsyncSession, clip: Clip, video: Video) -> bool:
    """클립 1건을 영상 presign, 프레임 추출, 저장소 업로드, 키 기록 순서로 처리한다.

    추출할 파일은 `thumbnail_source.source_for`가 고른다. 비식별화를 쓰는 영상은 비식별화본이다.

    Returns:
        bool: 성공하면 True. 실패하면 `_mark_failure`로 기록하고 False를 돌려준다.
            실패한 클립은 다음 주기에 다시 시도한다.
    """
    picked = await thumbnail_source.source_for(db, video)
    if picked is None:
        # 비식별화본이 확정되기 전이라 기다린다. 실패가 아니므로 기록하지 않는다.
        return False
    src, origin = picked
    try:
        bucket, key = _parse_s3_uri(src)
    except Exception:  # noqa: BLE001
        await _mark_failure(db, clip, "영상 파일 위치를 읽지 못했습니다")
        return False

    await storage.ensure_loaded(db, video.extra)
    url = storage.presign_get_for(bucket=bucket, key=key)
    start, end = float(clip.start_seconds), float(clip.end_seconds)
    ts = start + min(0.5, max(0.0, (end - start) / 2))  # 시작 직후의 검은 프레임을 피한다
    try:
        body = _extract_frame(url, ts)
    except FrameError as e:
        await _mark_failure(db, clip, str(e), transient=e.transient)
        return False

    thumb_key = f"{_THUMB_PREFIX}/{clip.clip_id}.jpg"
    try:
        # 클립이 속한 영상의 저장소(영상에 고정 기록한 연결·버킷)에 올린다.
        store = storage.for_video(video.extra)
        storage.put_bytes(thumb_key, body, "image/jpeg", store=store)
    except Exception as e:  # noqa: BLE001 — 업로드 실패도 일시가 섞인다(스로틀·순단)
        # 원문 예외(엔드포인트·RequestId)는 로그에만 남기고 사유에는 무엇이 실패했는지만 쓴다.
        # `operator_reason` 문구는 원문이 감사 로그에 있다고 안내하는데, 이 원문은 앱 로그에만 있다.
        log.warning("clip_thumbnail 업로드 실패 clip=%s: %s", clip.clip_id, e)
        await _mark_failure(
            db, clip, "썸네일을 저장소에 올리지 못했습니다", transient=is_transient_boto(e))
        return False
    # 성공하면 키를 기록하고 이전 실패 기록을 지운다. 병합과 삭제는 DB에서 한다.
    await clip_service.merge_extra(
        db, clip.clip_id, {_KEY: thumb_key, BUCKET_KEY: store.bucket, _FROM_KEY: origin},
        remove=(_ERROR_KEY, _RETRY_KEY, _FAILED_KEY, _STREAK_KEY, _RETRY_AFTER_KEY),
    )
    return True


async def process_pending(
    db: AsyncSession, limit: int = BATCH_LIMIT, budget_s: float = TIME_BUDGET_S,
) -> int:
    """썸네일 없는 클립을 상한과 시간 예산 안에서 처리하고 성공 건수를 돌려준다.

    beat 태스크 진입점이다. 먼저 다시 추출할 썸네일의 키를 지운다(`invalidate_unmasked`).
    다른 실행이 돌고 있으면 0을 돌려준다. 그래서 한 실행이 예산을 넘겨 다음 주기와 겹쳐도
    q.media 워커의 두 슬롯을 모두 차지하지 않는다.
    """
    async with single_runner(LOCK_KEY) as got:
        if not got:
            return 0
        if await invalidate_unmasked(db):
            await db.commit()
        pending = await list_pending(db, limit=limit)
        if not pending:
            return 0
        count = await run_within_budget(
            pending, lambda row: _process_one(db, row[0], row[1]),
            budget_s=budget_s, name="clip_thumbnail")
        await db.commit()
        return count


async def backfill(
    db: AsyncSession, *, force: bool = False, project_id: UUID | None = None,
    on_each: Callable[[UUID, bool], None] | None = None,
) -> tuple[int, int]:
    """정기 작업과 같은 규칙으로 클립 썸네일을 한꺼번에 만든다. 운영 스크립트가 부른다.

    원천 선택, 출처 기록(`scene_thumbnail_from`), 실패 정책은
    정기 작업의 `_process_one`을 그대로 쓴다.
    스크립트가 따로 추출하면 비식별화를 쓰는 영상의 썸네일을 원본에서 추출해 가리지 않은 얼굴이
    목록에 표시된다. 정기 작업과 같은 클립을 두 번 추출하지 않도록 같은 락을 잡는다.

    Args:
        force: 참이면 썸네일이 있거나 실패로 확정된 클립도 다시 만든다.
            거짓이면 정기 작업의 후보만 만든다.
        project_id: 이 프로젝트의 클립만 만든다. 없으면 전체다.
        on_each: 클립마다 (클립 ID, 성공 여부)로 부르는 함수. 스크립트가 진행 상황을 출력한다.

    Returns:
        (만든 수, 만들지 않은 수).
        만들지 않은 수에는 비식별화본을 기다리는 클립과 실패한 클립이 들어간다.

    Raises:
        BusyError: 정기 작업이 도는 중이라 락을 얻지 못한 경우.
    """
    async with single_runner(LOCK_KEY) as got:
        if not got:
            raise BusyError("clip_thumbnail 정기 작업이 도는 중이다")
        if await invalidate_unmasked(db):
            await db.commit()
        if force:
            q = (select(Clip.clip_id).join(Video, Video.video_id == Clip.video_id)
                 .where(Video.deleted_at.is_(None)))
        else:
            q = pending_select().with_only_columns(Clip.clip_id)
        if project_id is not None:
            q = q.where(Clip.project_id == project_id)
        ids = list(await db.scalars(q.order_by(Clip.created_at, Clip.clip_id)))
        done = 0
        for cid in ids:
            ok = await process_one_by_id(db, cid)
            done += ok
            if on_each:
                on_each(cid, ok)
        return done, len(ids) - done


async def process_one_by_id(db: AsyncSession, clip_id: UUID) -> bool:
    """클립 1건의 썸네일을 바로 만들고 커밋한다. 관리 스크립트와 테스트가 쓴다."""
    row = (await db.execute(
        select(Clip, Video).join(Video, Video.video_id == Clip.video_id)
        .where(Clip.clip_id == clip_id)
    )).first()
    if not row:
        return False
    ok = await _process_one(db, row[0], row[1])
    await db.commit()
    return ok
