"""원본 파일의 수명 관리. 파생물이 쓰임을 다한 뒤에 원본을 정리한다.

원본이 있으면 파생물은 캐시이고, 원본이 없으면 파생물이 유일한 사본이다.
분석본이 없을 때 원본을 지우면 분석본을 만들 방법이 사라진다.
그래서 원본은 분석 결과가 나온 뒤에만 정리한다. 분석 결과가 나온 상태는
`analysis_job.status`가 `job_state.PRODUCED`(완료·부분 완료)인 경우다.
그 뒤에는 원본을 읽는 단계가 없다.

정리 시점은 준비 과정에 연결하지 않고 beat가 주기적으로 대상을 고른다.
분석이 실패하거나 분석을 요청하지 않은 영상은 준비 과정이 멈춘 채로 원본을 남겨 둔다.

원본을 지우는 프로젝트(`retain_original=false`)는 체크섬이 끝날 때까지 정리를 미룬다.
체크섬은 원본을 읽어야 잴 수 있고, 받는 쪽이 납품 패키지를 원본과 대조하는 근거다.
정리와 체크섬이 같은 주기로 돌아서, 기다리지 않으면 정리가 먼저 원본을 지워 체크섬이 빈다.
체크섬을 끝내 구하지 못한 영상(`checksum.settled`의 상한)은 기다리지 않고 정리한다.
원본을 보관하는 프로젝트는 원본이 보관 위치로 옮겨져도 읽을 수 있어서 기다리지 않는다.

주의: `publish` 완료 안에서 원본을 정리하지 않는다.
원본이 이미 MI 규격(`h264 · 720p`)이면 변환이 분석본을 만들지 않고,
`resolver`가 `analysis_uri or source_uri`로 폴백해 publish 뒤의 MI 등록·분석이 원본을 읽는다.
publish 완료 때 원본을 지우면 기본 설정(`retain_original=false`)에서 가장 흔한 입력인
규격 안의 원본을 분석할 수 없다.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import AnalysisJob, Project, Video
from api_gateway.services.analysis import job_state
from api_gateway.services.joblock import single_runner
from api_gateway.services.media import checksum
from api_gateway.services.transient import is_transient_boto

log = logging.getLogger(__name__)

# 한 번에 정리할 영상 수의 상한. 정리는 급하지 않고 주기가 짧아서 남은 영상은 다음 실행이 처리한다.
_BATCH = 100


def _finished_source_query():
    """정리할 영상을 고르는 SELECT를 만든다. 정리 조건은 이 함수에만 둔다.

    테스트도 이 함수로 어떤 영상이 선택되는지 확인한다.
    전역 스캔이라 건수로 단언하면 다른 데이터에 따라 결과가 달라진다.
    """
    analyzed = (
        select(AnalysisJob.video_id)
        # 결과가 일부만 나온 상태(`partial`)도 분석 결과가 있는 것으로 센다.
        .where(AnalysisJob.status.in_(job_state.PRODUCED))
        .scalar_subquery()
    )
    # 보관 판정은 `_dispose_source`와 같은 열(`Project.retain_original`)을 읽는다.
    retaining = (
        select(Project.project_id)
        .where(Project.retain_original.is_(True))
        .scalar_subquery()
    )
    return (
        select(Video)
        .where(
            # 서버가 스테이징에 올린 파일만 고른다. 버킷에서 등록한 영상의 원본은
            # 원래 버킷에 있던 파일이라 지울 권한이 없다. `_dispose_source`도 같은 판정을 한다.
            Video.source_uri.like("s3://%/intake/%"),
            # 아직 정리하지 않은 파일. NULL은 정리 여부가 기록되지 않은 상태라서 포함한다.
            or_(Video.source_retained.is_(None), Video.source_retained.is_(True)),
            Video.deleted_at.is_(None),
            Video.video_id.in_(analyzed),
            # 지우는 원본은 체크섬이 끝난 뒤에 정리한다. 지운 원본은 다시 잴 수 없다.
            or_(Video.project_id.in_(retaining), checksum.settled()),
        )
        .limit(_BATCH)
    )


async def dispose_finished_sources(db: AsyncSession) -> int:
    """파생물이 할 일을 다 한 영상의 원본을 정리한다. 10분 주기 beat가 부른다.

    보관 정책(`retain_original`)에 따라 지우거나 보관 영역으로 옮긴다.
    보관 판정과 실제 삭제는 `publish._dispose_source`가 맡고, 이 함수는 정리 시점만 정한다.

    Returns:
        int: 처리한 영상 수.
    """
    from api_gateway.services.media.publish import _dispose_source

    # id 목록으로 돌고 반복마다 영상을 새로 읽는다.
    # `rollback()`은 `expire_on_commit=False`와 관계없이 세션의 ORM 객체를 모두 만료시킨다.
    # 객체를 미리 읽어 두면 한 건이 실패한 뒤 다음 반복의 속성 접근이 `MissingGreenlet`으로 실패해
    # 매 주기 같은 영상에서 배치 전체가 멈춘다.
    video_ids = list((await db.scalars(
        _finished_source_query().with_only_columns(Video.video_id)
    )).all())

    n = 0
    for vid in video_ids:
        video = await db.get(Video, vid)
        if video is None:  # 목록을 읽은 뒤 행이 지워진 경우. 정상 흐름이다
            continue
        try:
            detail = await _dispose_source(db, video)
        except Exception:  # noqa: BLE001 — 한 건 실패가 나머지를 막지 않는다
            log.exception("원본 정리 실패 video=%s", vid)
            await db.rollback()
            continue
        await db.commit()
        n += 1
        log.info("원본 정리 video=%s %s", vid, detail.get("source"))
    return n


# ── 분석본 정리: 같은 규칙을 파생물 쪽에 적용한다 ───────────────────────────────

# 분석본을 정리할 수 없는 사유 문장.
# 거절만 표시하면 운영자가 원인을 찾으려고 설정을 하나씩 확인해야 하므로
# 화면이 그대로 표시할 문장으로 돌려준다. 버튼 옆 토스트에 단독으로 뜨므로 주어를 쓴다.
_KEEP_NO_COPY = "분석본을 만들지 않았거나 이미 정리했습니다."
_KEEP_IN_USE = "아직 분석에 쓰입니다. 분석이 끝난 뒤에 정리할 수 있습니다."
_KEEP_ONLY_COPY = "원본이 없어 이 파일이 유일한 사본입니다. 지우면 되돌릴 수 없습니다."


def _analysis_blocker(video: Video, status: str | None) -> str | None:
    """분석본을 정리할 수 없는 사유를 돌려준다. 정리할 수 있으면 None이다.

    화면(버튼 표시 여부)과 API(요청 수락 여부)가 같은 판정을 쓰도록 판정은 이 함수에서만 한다.
    규칙이 두 곳에 있으면 버튼이 보이는데 누르면 409가 나거나, 정리할 수 있는데 버튼이 숨는다.
    """
    if not video.analysis_uri:
        return _KEEP_NO_COPY
    # 분석이 아직 이 사본을 읽는 경우. 결과가 나오지 않은 모든 상태가 해당한다.
    # 실패도 포함한다. 재시도가 등록부터 다시 돌 때 이 사본이 입력이 된다.
    if status not in job_state.PRODUCED:
        return _KEEP_IN_USE
    # 원본이 없으면 파생물이 유일한 사본이다. 읽는 단계가 없어도 지우면 다시 만들 수 없어 남긴다.
    if video.source_retained is False:
        return _KEEP_ONLY_COPY
    return None


async def analysis_blocker(db: AsyncSession, video: Video) -> str | None:
    """분석 상태를 조회해 `_analysis_blocker`를 부른다. 호출부가 조회를 직접 쓰지 않게 한다."""
    status = await db.scalar(
        select(AnalysisJob.status).where(AnalysisJob.video_id == video.video_id)
    )
    return _analysis_blocker(video, status)


async def purge_analysis(db: AsyncSession, video: Video) -> int:
    """영상의 분석본을 버킷에서 지우고 `analysis_uri`를 비운다.

    URI를 남기면 `resolver`가 없는 파일을 MI 입력으로 고른다.
    URI를 비우면 폴백이 원본을 가리켜 다시 분석할 수 있다.

    Returns:
        int: 회수한 바이트 수. 객체가 이미 없으면 0이다.

    Raises:
        HTTPException: 정리하면 안 되는 상태인 경우(409). 사유 문장을 그대로 싣는다.
    """
    from fastapi import HTTPException
    from fastapi import status as st

    blocker = await analysis_blocker(db, video)
    if blocker:
        raise HTTPException(st.HTTP_409_CONFLICT, blocker)

    from api_gateway.services.media import storage
    from api_gateway.services.media.video import _parse_s3_uri, merge_extra

    bucket, key = _parse_s3_uri(video.analysis_uri)
    freed = 0
    head = storage.head_in(bucket=bucket, key=key)
    if head is not None:
        freed = int(head.size_bytes or 0)
        storage.delete_object(bucket=bucket, key=key)
    video.analysis_uri = None
    # 정리한 시각을 남겨 분석본이 없는 상태가 의도한 정리인지 유실인지 구분한다.
    # `video.extra`는 S3 왕복 전에 읽은 값이라 통째로 치환하면 그사이 썸네일·probe 워커가
    # 같은 행에 쓴 키가 지워진다. 그래서 `merge_extra`로 병합한다.
    await merge_extra(
        db, video.video_id, {"analysis_purged_at": datetime.now(UTC).isoformat()}
    )
    log.info("분석본 정리 video=%s freed=%d", video.video_id, freed)
    return freed


# ── 고아 스테이징 정리: 보고 없이 끝난 시도가 남긴 파일을 지운다 ─────────────────

#: 단일 실행 락 키('RBSW'). 락 획득·해제 규약은 `services/joblock.py`에 있다.
_SWEEP_KEY: Final = 0x52425357
#: 시도 하나의 스테이징 키(`transcode.staging_key`)를 영상 id와 토큰으로 나누는 형식.
#: 토큰이 없는 마스터 키(`transcode.master_key`)는 이 형식에 맞지 않아 지우지 않는다.
_STAGING_RE: Final = re.compile(r"^work/([0-9a-f-]{36})/([0-9a-f]{32})/[^/]+$")
#: 한 실행이 지우는 파일 수의 상한. 남은 파일은 다음 실행이 지운다.
_SWEEP_MAX_DELETES = 2000
#: 한 실행의 시간 예산(초). 목록 조회와 삭제가 길어져도 다음 날 실행과 겹치지 않게 한다.
_SWEEP_BUDGET_S = 600
#: 목록 한 페이지의 키 수.
_SWEEP_PAGE = 1000


def _staging_min_age_s() -> int:
    """지울 수 있는 파일의 최소 경과 시간(초)을 돌려준다. 잡 URL 유효 기간보다 한 시간 길다.

    유효 기간 안의 파일은 아직 보고하지 않은 시도의 업로드일 수 있다.
    유효 기간이 지난 시도는 더 올릴 수 없으므로 새 파일이 생기지 않는다.
    """
    from api_gateway.services.media.transcode import _JOB_URL_TTL_S

    return _JOB_URL_TTL_S + 3600


async def _live_tokens(db: AsyncSession, video_ids: set[str]) -> dict[str, set[str]]:
    """이 DB에 있는 영상마다 지금 쓰이는 토큰 집합을 돌려준다.

    결과의 키는 이 DB에 행이 있는 영상 id 전부다(soft delete 포함).
    키에 없는 영상 id는 다른 설치본이 같은 버킷에 쓴 파일일 수 있어 정리하지 않는다.
    토큰 집합에 든 토큰의 스테이징 파일도 지우지 않는다.

    토큰은 진행 중인 변환·재생본·비식별화 시도의 토큰과 비식별화 회수 표식의 토큰이다.
    회수 표식의 토큰은 늦게 도착한 완료 보고가 그 시도의 산출을 이어받을 때 쓴다
    (`deident._adopt_late_result`).
    """
    if not video_ids:
        return {}
    rows = (await db.execute(
        select(Video.video_id, Video.transcode_token, Video.publish_token, Video.deident_token,
               Video.extra["deident_reclaimed"]["token"].astext)
        .where(Video.video_id.in_(list(video_ids)))
    )).all()
    return {str(vid): {tok for tok in tokens if tok} for vid, *tokens in rows}


async def _storage_pins(db: AsyncSession) -> list[dict]:
    """영상 행에 고정 기록한 저장소(연결 id와 버킷) 목록을 중복 없이 돌려준다.

    워커는 영상의 고정 기록 버킷(`storage.bucket_of`)에 올리므로 이 목록이 정리할 버킷 전부다.
    """
    from api_gateway.services.media import storage

    raw = (await db.scalars(
        select(Video.extra[storage.PIN_KEY]).where(Video.extra.has_key(storage.PIN_KEY))
        .distinct()
    )).all()
    seen: dict[tuple, dict] = {}
    for pin in raw:
        if isinstance(pin, dict) and pin.get("bucket"):
            seen.setdefault((pin.get("connection_id"), pin["bucket"]), pin)
    return list(seen.values())


async def sweep_orphan_staging(
    db: AsyncSession, *, prefix: str = "work/", now: datetime | None = None,
) -> int:
    """보고 없이 끝난 시도의 스테이징 파일을 지우고 지운 수를 돌려준다. 하루 한 번 beat가 부른다.

    완료 판정은 자기 토큰의 스테이징 파일을 옮기거나 지운다(`transcode.promote_staged`·
    `discard_rejected`). 워커가 보고 없이 비정상 종료된 시도의 파일은 정리하는 곳이 없다.
    앱 경로에는 버킷 만료 규칙을 두지 않으므로(`docs/install.md`) 이 함수가 지운다.

    지우는 파일은 네 조건을 모두 만족해야 한다.
    키가 `work/{video_id}/{token}/파일` 형식이다. 그 영상 id가 이 DB의 영상이다(soft delete 포함).
    그 토큰이 현재 시도와 회수 표식의 토큰 목록 밖에 있다(`_live_tokens`).
    마지막 수정 시각이 잡 URL 유효 기간보다 오래됐다(`_staging_min_age_s`).

    이 DB에 없는 영상 id의 파일은 남긴다. 버킷을 다른 설치본과 같이 쓰면 `work/` 아래에
    그 설치본의 진행 중인 시도가 있고, 이 DB에는 그 시도의 토큰이 없다.

    버킷마다 그 버킷을 고른 연결로 읽는다(`storage.ensure_loaded`). 한 버킷의 목록 조회가
    실패하면 그 버킷만 다음 실행으로 미룬다. 삭제가 실패한 파일도 다음 실행이 다시 고른다.
    지우는 수(`_SWEEP_MAX_DELETES`)와 시간(`_SWEEP_BUDGET_S`)에 상한을 둔다.

    Args:
        prefix: 목록을 읽을 접두사. 테스트가 한 영상의 접두사로 좁힐 때 쓴다.
        now: 경과 시간을 잴 기준 시각. 테스트가 유효 기간이 지난 시점을 만들 때 쓴다.

    Returns:
        int: 지운 파일 수. 다른 인스턴스가 실행 중이면 0이다.
    """
    from api_gateway.services.media import storage

    async with single_runner(_SWEEP_KEY) as got:
        if not got:
            return 0
        cutoff = (now or datetime.now(UTC)) - timedelta(seconds=_staging_min_age_s())
        started = time.monotonic()
        deleted = 0
        for pin in await _storage_pins(db):
            extra = {storage.PIN_KEY: pin}
            await storage.ensure_loaded(db, extra)
            store = storage.for_video_or_none(extra)
            if store is None:
                log.warning("고아 스테이징 정리 건너뜀: 저장소 연결을 읽지 못함 bucket=%s",
                            pin["bucket"])
                continue
            deleted += await _sweep_bucket(db, store, pin["bucket"], prefix, cutoff,
                                           started, _SWEEP_MAX_DELETES - deleted)
            if deleted >= _SWEEP_MAX_DELETES or time.monotonic() - started > _SWEEP_BUDGET_S:
                break
        if deleted:
            log.info("고아 스테이징 정리 %d건", deleted)
        return deleted


async def _sweep_bucket(
    db: AsyncSession, store, bucket: str, prefix: str, cutoff: datetime,
    started: float, budget: int,
) -> int:
    """버킷 하나의 고아 스테이징 파일을 지우고 지운 수를 돌려준다.

    목록을 페이지마다 읽고, 페이지에 나온 영상의 현재 토큰을 한 번에 조회한다.
    이 DB에 없는 영상 id의 파일은 지우지 않는다.
    목록 조회가 실패하면 이 버킷의 남은 페이지를 다음 실행으로 미룬다.
    삭제가 일시 오류로 실패하면 저장소가 요청을 받지 못하는 상태로 보고 이 버킷을 멈춘다.
    """
    from api_gateway.services.media import storage

    deleted = 0
    token: str | None = None
    while deleted < budget and time.monotonic() - started <= _SWEEP_BUDGET_S:
        try:
            page = storage.browse_bucket(prefix=prefix, max_keys=_SWEEP_PAGE, recursive=True,
                                         token=token, bucket=bucket, store=store)
        except Exception as exc:  # noqa: BLE001 — 한 버킷의 실패가 다른 버킷을 막지 않는다
            log.warning("고아 스테이징 목록 조회 실패 bucket=%s (%s): %s", bucket,
                        "일시 오류" if is_transient_boto(exc) else "읽기 실패", exc)
            return deleted
        old = []
        for f in page["files"]:
            m = _STAGING_RE.match(f["key"])
            modified = f.get("last_modified")
            if m and isinstance(modified, datetime) and modified < cutoff:
                old.append((f["key"], m.group(1), m.group(2)))
        live = await _live_tokens(db, {vid for _, vid, _ in old})
        for key, vid, tok in old:
            # 이 DB에 없는 영상의 파일이거나 지금 쓰이는 토큰의 파일은 남긴다.
            if vid not in live or tok in live[vid]:
                continue
            if deleted >= budget:
                break
            try:
                storage.delete_object(bucket=bucket, key=key, store=store)
            except Exception as exc:  # noqa: BLE001 — 실패한 파일은 다음 실행이 다시 고른다
                log.warning("고아 스테이징 삭제 실패 key=%s: %s", key, exc)
                if is_transient_boto(exc):
                    return deleted
                continue
            deleted += 1
        if not page["truncated"]:
            break
        token = page["next_token"]
    return deleted
