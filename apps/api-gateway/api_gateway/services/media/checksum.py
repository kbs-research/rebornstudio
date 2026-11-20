"""원본 sha256 체크섬을 채우는 주기 작업.

납품 패키지에는 미디어가 없고, 클립은 받는 쪽이 가진 원본의 구간을 타임코드로 가리킨다.
받는 쪽 원본이 분석한 파일과 다르면 모든 시각이 어긋난다.
체크섬은 두 파일이 같은지 확인하는 근거다.

S3 ETag 대신 sha256을 쓴다. 멀티파트 업로드의 ETag는 파트 크기에 따라 값이 달라서
받는 쪽이 같은 파트 크기로 나누지 않으면 재현할 수 없다. sha256은 `sha256sum`으로 재현할 수 있다.

전체 바이트를 읽어야 해서 영상 하나에 수십 초가 걸린다. 그래서 등록 응답에서 재지 않고
원본이 남아 있는 동안 beat가 주기마다 채운다.
원본 정리(`retention`)는 같은 주기로 돌기 때문에 `settled()`로 체크섬이 끝났는지 확인하고 기다린다.

체크섬이 없어도 내보내기는 막지 않는다. 없는 영상은 `null`로 내보내고 리포트가 그 건수를 센다.

영구 실패는 원본 객체가 없는 응답(NoSuchKey·404)과 원본 위치를 해석할 수 없는 경우다.
영구 실패가 `_MAX_RETRIES`번이면 후보에서 뺀다. 상한이 없으면 원본이 지워진 영상이
beat마다 traceback을 로그에 쌓는다.
일시 오류(스로틀·순단)와 자격증명·권한 오류는 세지 않고 다음 주기에 다시 시도한다.
자격증명·권한 오류는 연결 설정을 고치면 다시 읽을 수 있다. 세어서 포기하면
`settled()`가 참이 되어 원본 정리가 체크섬 없이 원본을 지운다.
실패 사유는 `extra.checksum_error`에 화면 문구로 남긴다.

실패한 영상은 시도 시각(`extra.checksum_tried_at`)을 남기고 후보 순서의 뒤로 보낸다.
시도하지 않은 영상이 먼저이고, 시도한 영상은 오래전에 시도한 순서다.
등록 순서로만 고르면 연결이 끊긴 프로젝트의 영상이 매 주기 배치를 채워 다른 원본의 체크섬이 멈춘다.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime

from botocore.exceptions import ClientError
from sqlalchemy import func, not_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import Video
from api_gateway.services import error_text
from api_gateway.services.joblock import single_runner
from api_gateway.services.media import storage
from api_gateway.services.media.video import merge_extra
from api_gateway.services.transient import is_transient_boto

log = logging.getLogger(__name__)

# 단일 실행 락 키('RBCS'). 락 획득·해제 규약은 services/joblock.py에 있다.
# 전체 바이트를 읽는 작업이라 두 인스턴스가 함께 돌면 대역폭만 두 배로 쓴다.
_ADVISORY_KEY = 0x52424353

# 읽기 청크 크기. 너무 작으면 호출 수가 늘고, 너무 크면 메모리 사용이 는다.
_CHUNK = 8 * 1024 * 1024

# 영구 실패 상한. 닿으면 후보에서 빠진다. 일시 오류(transient)는 세지 않는다.
_MAX_RETRIES = 3
# 영구 실패 횟수를 담는 `extra` 키
_FAIL_KEY = "checksum_failures"
#: 영상 상세 `파일` 묶음이 표시하는 실패 사유의 키. 값은 운영자용 화면 문구다.
ERROR_KEY = "checksum_error"
#: 실패로 끝난 마지막 시도 시각(ISO)의 `extra` 키. 후보 순서를 정한다.
_TRIED_KEY = "checksum_tried_at"
# 원본 객체나 버킷이 없다는 S3 오류 코드. 다시 읽어도 결과가 같아 영구 실패로 센다.
_GONE_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "NotFound", "404"})


def sha256_of(*, bucket: str, key: str) -> str:
    """객체 전체를 디스크에 내려받지 않고 스트림으로 읽어 sha256을 계산한다.

    반환 형식은 `sha256:<hex>`다. 받는 쪽이 알고리즘을 알 수 있게 이름을 앞에 붙인다.
    패키지 색인의 `checksum`과 같은 형식이다.
    """
    body = storage.get_object_body(bucket=bucket, key=key)
    h = hashlib.sha256()
    for chunk in iter(lambda: body.read(_CHUNK), b""):
        h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _is_gone(exc: BaseException) -> bool:
    """원본 객체나 버킷이 없다는 응답이면 참이다. 참인 실패만 영구 실패로 센다."""
    if not isinstance(exc, ClientError):
        return False
    err = exc.response.get("Error") or {}
    meta = exc.response.get("ResponseMetadata") or {}
    return err.get("Code") in _GONE_CODES or meta.get("HTTPStatusCode") == 404


def _gave_up():
    """영구 실패가 상한에 닿아 더 시도하지 않는 영상의 조건을 만든다."""
    return func.coalesce(Video.extra[_FAIL_KEY].as_integer(), 0) >= _MAX_RETRIES


def settled():
    """체크섬을 기록했거나 더 시도하지 않는 영상의 조건을 만든다. 원본 정리가 이 조건을 기다린다.

    상한에 닿은 영상도 포함한다.
    포함하지 않으면 체크섬을 구하지 못한 원본이 스테이징에 영구히 남는다.
    일시 오류는 세지 않으므로 저장소 장애가 이어지는 동안에는 원본 정리도 기다린다.
    """
    return or_(Video.source_sha256.is_not(None), _gave_up())


def _pending_query(limit: int | None):
    """체크섬을 아직 재지 않은 영상을 고르는 쿼리를 만든다. 원본이 남아 있는 영상만 대상이다.

    순서는 시도하지 않은 영상이 먼저이고, 실패한 영상은 오래전에 시도한 순서다.
    """
    return (
        # 행을 ORM 객체 대신 값으로 읽는다. ORM 객체로 읽으면 한 건의 `rollback()`이
        # 나머지 객체를 모두 만료시켜 다음 반복의 속성 접근이 `MissingGreenlet`으로 실패한다.
        select(Video.video_id, Video.source_uri, Video.extra,
               Video.extra[_FAIL_KEY].as_integer())
        .where(
            Video.source_sha256.is_(None),
            Video.source_uri.is_not(None),
            Video.source_retained.is_not(False),
            Video.deleted_at.is_(None),
            # 영구 실패가 상한에 닿은 영상은 뺀다.
            not_(_gave_up()),
        )
        .order_by(Video.extra[_TRIED_KEY].astext.asc().nulls_first(), Video.created_at)
        .limit(limit)   # None이면 전체. 남은 수를 셀 때 쓴다.
    )


def _parse_uri(uri: str) -> tuple[str, str] | None:
    """`s3://bucket/key`(또는 `ncp://`)를 (bucket, key)로 나눈다. 허용 scheme은 resolver와 같다."""
    for scheme in ("s3://", "ncp://"):
        if uri.startswith(scheme):
            bucket, _, key = uri[len(scheme):].partition("/")
            if bucket and key:
                return bucket, key
    return None


async def _count_failure(db: AsyncSession, video_id, failures: int | None, reason: str) -> int:
    """영구 실패 횟수를 하나 올리고 화면 사유를 남긴 뒤 커밋한다. 올린 뒤의 횟수를 돌려준다.

    `extra`는 `merge_extra`로 병합한다. 사유는 화면 문구로 저장하고 원문은 로그에 남긴다.
    시도 시각도 남겨 후보 순서의 뒤로 보낸다. 다시 읽어도 같은 실패가 나는 영상이
    배치 앞쪽을 차지하지 않게 한다.
    """
    n_fail = int(failures or 0) + 1
    await merge_extra(db, video_id, {_FAIL_KEY: n_fail, ERROR_KEY: reason,
                                     _TRIED_KEY: datetime.now(UTC).isoformat()})
    await db.commit()
    return n_fail


async def fill_missing_checksums(db: AsyncSession, limit: int | None = None) -> int:
    """체크섬이 없는 원본의 sha256을 채우고 처리한 건수를 돌려준다. beat 진입점이다.

    한 실행의 처리량은 개수와 시간 예산(`checksum_budget_seconds`)으로 함께 제한한다.
    파일 크기가 100배까지 차이 나서 개수만으로는 실행 시간을 예측할 수 없다.
    실행이 beat 주기를 넘기면 겹친 다음 실행은 advisory lock에 막혀 아무 일도 하지 못한다.
    예산을 넘기면 처리 중인 건까지만 끝낸다.

    한 건이 실패해도 나머지는 계속 처리한다.
    실패한 영상은 값이 `NULL`로 남아 다음 beat가 다시 고른다.
    """
    async with single_runner(_ADVISORY_KEY) as got:
        if not got:
            return 0   # 다른 인스턴스가 실행 중이다
        batch = limit if limit is not None else settings.checksum_batch
        budget = settings.checksum_budget_seconds
        started = time.monotonic()
        videos = (await db.execute(_pending_query(batch))).all()
        n = 0
        for video_id, source_uri, extra, failures in videos:
            if time.monotonic() - started > budget:
                log.info(
                    "체크섬 예산 소진 — %s/%s건에서 멈춤 (다음 beat가 이어서 집는다)",
                    n, len(videos),
                )
                break
            parsed = _parse_uri(source_uri or "")
            if not parsed:
                await _count_failure(db, video_id, failures, "원본 위치를 해석하지 못했습니다")
                log.warning("체크섬 실패 — 알 수 없는 URI video=%s", video_id)
                continue
            bucket, key = parsed
            try:
                # 워커 메모리에 영상의 저장소 연결이 아직 없으면 읽기가 자격증명 오류로 실패한다.
                await storage.ensure_loaded(db, extra)
                digest = sha256_of(bucket=bucket, key=key)
            except Exception as exc:  # noqa: BLE001 — boto3·네트워크 등 광범위
                if not _is_gone(exc):
                    # 일시 오류와 자격증명·권한 오류는 세지 않는다. 원본 정리도 기다린다.
                    # 시도 시각을 남겨 후보 순서의 뒤로 보낸다.
                    kind = "일시 오류" if is_transient_boto(exc) else "읽기 실패"
                    log.warning("체크섬 %s — 다음 주기 video=%s: %s", kind, video_id, exc)
                    await merge_extra(db, video_id, {_TRIED_KEY: datetime.now(UTC).isoformat()})
                    await db.commit()
                    continue
                n_fail = await _count_failure(
                    db, video_id, failures,
                    error_text.operator_text(exc) or "원본을 읽지 못했습니다")
                log.exception("체크섬 실패(%d/%d) video=%s", n_fail, _MAX_RETRIES, video_id)
                continue
            # `source_sha256` 열만 UPDATE해 다른 워커가 같은 행에 쓴 값을 덮어쓰지 않는다.
            await db.execute(
                update(Video).where(Video.video_id == video_id).values(source_sha256=digest)
            )
            if failures or (extra or {}).get(_TRIED_KEY):
                # 앞선 실패 기록을 지운다. 남아 있으면 화면이 계속 "계산 실패"를 표시한다.
                await merge_extra(db, video_id, {}, remove=(_FAIL_KEY, ERROR_KEY, _TRIED_KEY))
            await db.commit()
            n += 1
            log.info("원본 체크섬 video=%s %s", video_id, digest)
        if n:
            # 남은 작업량을 로그로 가늠할 수 있게 남은 수도 기록한다.
            # 부분 인덱스가 있어 비용이 작다.
            left = await db.scalar(
                select(func.count()).select_from(_pending_query(None).subquery())
            )
            log.info("원본 체크섬 %s건 완료 — 남은 %s건", n, left)
        return n
