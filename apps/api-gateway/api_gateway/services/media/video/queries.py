"""영상 목록·단건 조회와 상태별 집계."""
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import (
    IngestBucketSpan,
    IngestJobIssue,
    VideoBucketCounts,
    VideoDTO,
    VideoSelectRequest,
    VideoSelectResult,
)
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import Clip, Project, User, Video, WorkItem
from api_gateway.services import authz, text_search
from api_gateway.services.media import resolver, storage, video_thumbnail
from api_gateway.services.media.video.access import _require_member
from api_gateway.services.media.video.classify import (
    _ingest_bucket_case,
    _maiu_requested_at_expr,
    _pipeline_stage_case,
    _prep_blocked_stage_case,
    _prep_stage_case,
)
from api_gateway.services.media.video.dto import LibraryStatus, _redact_for_labeler, _to_dto
from api_gateway.services.work import service as work_service


def _matches(like: str):
    """검색 패턴이 제목·원본 경로·방송사·프로그램명 중 하나와 맞으면 참인 조건을 만든다.

    목록과 조건 선택(`select_ids`)이 같은 조건을 써야 "N건 선택"과 목록이 같은 영상을 가리킨다.
    패턴은 `text_search.contains`가 이스케이프해서 `%`·`_`를 문자 그대로 비교한다.
    """
    esc = text_search.ESCAPE
    return or_(
        Video.title.ilike(like, escape=esc),
        Video.source_uri.ilike(like, escape=esc),
        Video.broadcaster.ilike(like, escape=esc),
        Video.program_name.ilike(like, escape=esc),
    )


def _poster_url(v: Video) -> str | None:
    """영상 포스터 썸네일 URL을 돌려준다.

    video_thumbnail이 만든 프레임 키가 있으면 요청마다 새로 서명한다.
    첫 클립 썸네일이 없을 때(등록 직후, MAIU 분석 전) 대표 이미지로 쓴다.
    올린 버킷(`poster_thumbnail_bucket`) 기록이 없거나, 영상의 고정 버킷과 다르거나,
    키가 썸네일 워커 형식(`video_thumbnail.poster_key`)이 아니면 주소를 만들지 않는다.
    그래서 `extra`에 다른 경로로 들어간 값으로 다른 버킷·다른 영상의 객체를 서명하지 않는다.
    """
    extra = v.extra or {}
    key = extra.get("poster_thumbnail_key")
    bucket = extra.get(video_thumbnail.BUCKET_KEY)
    pin = storage.pin_of(extra)
    if not key or pin is None or not bucket or bucket != pin.bucket:
        return None
    if key != video_thumbnail.poster_key(v.video_id):
        return None
    return storage.presign_get_or_none(key, bucket=bucket)



async def _first_clip_thumbnails(
    db: AsyncSession, video_ids: list[UUID]
) -> dict[UUID, str | None]:
    """영상별 대표 썸네일을 N+1 없이 한 번에 조회한다.

    대표 썸네일은 각 영상의 첫 클립(start_seconds 최소)의 씬 썸네일이다.
    클립 썸네일과 같은 규칙을 쓴다(앱 버킷 키를 새로 서명하고, 없으면 레거시 URL).
    """
    if not video_ids:
        return {}
    from api_gateway.services.media.clip import _thumbnail_url  # 지연 import로 순환을 피한다
    rows = await db.execute(
        select(Clip.video_id, Clip.extra)
        .distinct(Clip.video_id)
        .where(Clip.video_id.in_(video_ids))
        .order_by(Clip.video_id, Clip.start_seconds)
    )
    return {vid: _thumbnail_url(extra or {}) for vid, extra in rows}


async def _library_status(
    db: AsyncSession, video_ids: list[UUID]
) -> dict[UUID, LibraryStatus]:
    """영상별 파생 상태를 N+1 없이 한 번에 조회한다.

    MI 배지, 반입 상태, 다음 단계 대기량을 모두 한 쿼리로 계산한다.
    목록 필터·집계와 규칙을 맞추려고 판정은 SQL CASE(`_ingest_bucket_case`)로 한다.
    """
    if not video_ids:
        return {}
    clip_count = (
        select(func.count()).select_from(Clip)
        .where(Clip.video_id == Video.video_id).correlate(Video).scalar_subquery()
    )

    def _clip_work(*statuses: str):
        """영상의 클립 중 주어진 work 상태에 있는 수를 세는 식을 만든다.

        화면이 분석이 끝난 클립의 단계를 표시할 때 쓴다.
        """
        return (
            select(func.count()).select_from(WorkItem)
            .where(
                WorkItem.video_id == Video.video_id,
                WorkItem.clip_id.is_not(None),
                WorkItem.status.in_(statuses),
            ).correlate(Video).scalar_subquery()
        )

    rows = (await db.execute(
        select(
            Video.video_id,
            clip_count.label("clips"),
            _pipeline_stage_case().label("stage"),
            _ingest_bucket_case().label("bucket"),
            _prep_stage_case().label("prep"),
            _prep_blocked_stage_case().label("prep_blocked"),
            # 분석이 끝난 영상의 클립을 단계별로 센다. 화면이 다음 단계를 정할 때 쓴다.
            _clip_work("available", "claimed").label("labeling"),
            _clip_work("submitted").label("reviewing"),
            _clip_work("approved").label("approved"),
            # 삭제하면 닫힐 작업 수. 삭제 확인 창이 표시한다.
            # 위 세 수의 합에는 `rejected`(재작업 중)가 빠지므로 따로 센다.
            # 미완 판정은 `work.service._CLOSABLE` 하나로 한다.
            _clip_work(*work_service._CLOSABLE).label("open_work"),
        ).where(Video.video_id.in_(video_ids))
    )).all()
    return {
        r.video_id: LibraryStatus(
            clip_count=int(r.clips or 0),
            pipeline_stage=r.stage,
            ingest_bucket=r.bucket,
            prep_stage=r.prep,
            # 막힌 위치는 prep가 `blocked`인 행에서만 쓰므로 다른 행에는 `None`을 싣는다.
            prep_blocked_stage=r.prep_blocked if r.prep == "blocked" else None,
            labeling_clips=int(r.labeling or 0),
            reviewing_clips=int(r.reviewing or 0),
            approved_clips=int(r.approved or 0),
            open_work_items=int(r.open_work or 0),
        )
        for r in rows
    }


async def list_for_project(
    db: AsyncSession,
    project_id: UUID,
    user: User,
    *,
    bucket: str | None = None,
    prep: str | None = None,
    batch: UUID | None = None,
    q_text: str | None = None,
    limit: int = 50,
    before_at: datetime | None = None,
    before_id: UUID | None = None,
) -> list[VideoDTO]:
    """프로젝트의 영상 목록을 최신순 커서 페이지로 돌려준다(대표 썸네일 포함).

    멤버만 볼 수 있다.
    라벨러에게는 `source_uri`·`metadata`를 비운 응답을 준다(`_redact_for_labeler`).
    영상마다 클립·MAIU·티켓 파생 상태가 함께 실려서 전량 조회는 브라우저를 멈춘다.
    그래서 `(created_at, video_id)` 복합 커서로 나눠 보낸다(clips·audit events와 같은 방식).
    같은 시각에 생성된 행이 흔하므로 `video_id`로 순서를 정한다.

    Args:
        bucket: 반입 상태 필터(`_ingest_bucket_case`의 값). None이면 전체.
        prep: 준비 여정 필터(`_prep_stage_case`의 값).
            여정 스트립 셀·묶음 세그먼트를 누를 때 쓴다.
        batch: 반입 묶음 ID. 방금 올린 묶음만 볼 때 쓴다.
        q_text: 제목·원본 경로·방송사·프로그램명 부분 일치 검색어(대소문자 무시).
            화면이 받은 행만 검색하면 다른 페이지의 결과가 빠지므로 서버에서 거른다.
        before_at/before_id: 직전 페이지 마지막 항목의 커서.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 `project_not_found`),
            멤버가 아닌 경우(403 `not_a_member`).
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await _require_member(db, project_id, user)

    q = select(Video).where(Video.project_id == project_id, Video.deleted_at.is_(None))
    if bucket:
        q = q.where(_ingest_bucket_case() == bucket)
    if prep:
        q = q.where(_prep_stage_case() == prep)
    if batch:
        q = q.where(Video.upload_batch_id == batch)
    like = text_search.contains(q_text)
    if like:
        q = q.where(_matches(like))
    if before_at is not None:
        # 같은 created_at이 여럿이면 id로 구분해 커서가 같은 위치를 반복하지 않게 한다.
        q = q.where(
            or_(
                Video.created_at < before_at,
                and_(Video.created_at == before_at, Video.video_id < before_id)
                if before_id
                else Video.created_at < before_at,
            )
        )
    rows = list(await db.scalars(
        q.order_by(Video.created_at.desc(), Video.video_id.desc()).limit(limit)
    ))

    ids = [v.video_id for v in rows]
    thumbs = await _first_clip_thumbnails(db, ids)
    lib = await _library_status(db, ids)
    dtos = [
        _to_dto(v, thumbnail_url=thumbs.get(v.video_id) or _poster_url(v), lib=lib.get(v.video_id))
        for v in rows
    ]
    # 상세 조회와 같은 가림 규칙을 쓴다. 상세만 가리면 같은 값이 목록으로 노출된다.
    if not await authz.is_work_result_viewer(db, project_id, user):
        return [_redact_for_labeler(d) for d in dtos]
    return dtos


# 조건으로 고르는 id의 상한. 넘으면 상한까지만 돌려주고 `truncated`를 참으로 표시한다.
_SELECT_CAP = 2000


async def select_ids(
    db: AsyncSession, project_id: UUID, user: User, payload: VideoSelectRequest
) -> VideoSelectResult:
    """조건으로 영상을 선택한다. 조건에 맞는 영상 id를 상한(`_SELECT_CAP`)까지 돌려준다.

    전체 수(`total`)와 상한에서 잘렸는지(`truncated`)도 함께 돌려준다.
    커서 목록에는 불러온 행만 있으므로 조건에 맞는 영상 전부를 처리하려면 서버가 id 목록을 만든다.
    판정은 목록·집계와 같은 CASE라 칩에 표시된 수와 여기서 나오는 수가 같다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 `project_not_found`),
            멤버가 아닌 경우(403 `not_a_member`).
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await _require_member(db, project_id, user)

    q = select(Video.video_id).where(
        Video.project_id == project_id, Video.deleted_at.is_(None)
    )
    if payload.batch:
        q = q.where(Video.upload_batch_id == payload.batch)
    if payload.prep:
        q = q.where(_prep_stage_case() == payload.prep)
        # 이 단계의 영상을 모두 담는다. 더 거르면 칩·레일의 분모와 일괄 처리 대상이 어긋난다.
    if payload.blocked_stage:
        # 이슈 위치까지 좁힌다. 막힌 영상 중 그 위치의 영상만 담는다(레일 액션의 대상 정의).
        q = q.where(
            _prep_stage_case() == "blocked",
            _prep_blocked_stage_case() == payload.blocked_stage,
        )
    if payload.bucket:
        q = q.where(_ingest_bucket_case() == payload.bucket)
    like = text_search.contains(payload.q)
    if like:
        q = q.where(_matches(like))
    if payload.exclude_ids:
        q = q.where(Video.video_id.notin_(payload.exclude_ids))

    total = int(await db.scalar(
        select(func.count()).select_from(q.subquery())
    ) or 0)
    ids = list(await db.scalars(
        q.order_by(Video.created_at.desc(), Video.video_id.desc()).limit(_SELECT_CAP)
    ))
    return VideoSelectResult(video_ids=ids, total=total, truncated=total > len(ids))


async def ingest_bucket_totals(db: AsyncSession, project_id: UUID) -> dict[str, int]:
    """반입 상태 분류별 건수만 센다. 권한 검사도 부가 집계도 없는 순수 계산이다.

    `bucket_counts`와 같은 `_ingest_bucket_case`를 쓴다.
    `bucket_counts`는 화면이 부르는 엔드포인트용이고(멤버 검사, 복구 가능 수, 이슈 분해 포함),
    이 함수는 권한 검사를 마친 서버 내부 코드(`analytics.pipeline_summary`)가 쓴다.
    """
    rows = (await db.execute(
        select(_ingest_bucket_case().label("bucket"), func.count())
        .where(Video.project_id == project_id, Video.deleted_at.is_(None))
        .group_by("bucket")
    )).all()
    counts = {b: 0 for b in ("preparing", "ready", "analyzing", "done", "blocked")}
    for name, n in rows:
        counts[name] = int(n)
    return counts


async def bucket_counts(
    db: AsyncSession, project_id: UUID, user: User, *, batch: UUID | None = None
) -> VideoBucketCounts:
    """반입 상태별 건수, 복구 가능 수, 이슈 위치별 분해를 돌려준다.

    목록은 페이지로 나뉘므로 화면에 있는 행만 세면 필터 배지가 잘못된 수를 표시한다.
    판정은 목록과 같은 `_ingest_bucket_case`를 써서 배지와 목록의 수를 맞춘다.

    `issues`도 같은 이유로 서버가 센다.
    이슈 레인의 일괄 버튼은 `검증 재검사 N건`처럼 이름에 수를 적고,
    버튼이 처리하는 대상은 같은 조건의 `select_ids` 결과라 여기의 수와 같다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 `project_not_found`),
            멤버가 아닌 경우(403 `not_a_member`).
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await _require_member(db, project_id, user)

    scope = [Video.project_id == project_id, Video.deleted_at.is_(None)]
    if batch:
        scope.append(Video.upload_batch_id == batch)

    # 건수와 함께 분류별 길이 합·길이 정보가 없는 영상 수·가장 이른 분석 요청 시각을 한 번에 센다.
    # 화면이 불러온 행으로 길이를 더하면 커서 뒤의 영상이 빠진다.
    rows = (await db.execute(
        select(
            _ingest_bucket_case().label("bucket"),
            func.count(),
            func.coalesce(func.sum(Video.duration_seconds), 0.0),
            func.count().filter(Video.duration_seconds.is_(None)),
            func.min(_maiu_requested_at_expr()),
        )
        .where(*scope).group_by("bucket")
    )).all()
    counts = {b: 0 for b in ("preparing", "ready", "analyzing", "done", "blocked")}
    spans = {b: IngestBucketSpan(bucket=b) for b in counts}
    for name, n, secs, unmeasured, oldest in rows:
        counts[name] = int(n)
        spans[name] = IngestBucketSpan(
            bucket=name, total_seconds=round(float(secs or 0.0), 3),
            unmeasured=int(unmeasured or 0), oldest_requested_at=oldest,
        )

    # 이슈 위치별 건수. Job 상세(`ingest_batch.get_detail`)와 같은 CASE, 같은 정렬을 쓴다.
    issue_rows = (await db.execute(
        select(_prep_blocked_stage_case().label("at"), func.count())
        .where(*scope, _prep_stage_case() == "blocked")
        .group_by("at").order_by(func.count().desc())
    )).all()

    return VideoBucketCounts(
        **counts,
        total=sum(counts.values()),
        issues=[IngestJobIssue(stage=at, count=int(n)) for at, n in issue_rows],
        spans=list(spans.values()),
    )


async def get_one(db: AsyncSession, video_id: UUID, user: User) -> VideoDTO:
    """영상 1건을 조회한다. 멤버 또는 super_admin만 볼 수 있다.

    라벨러에게는 `source_uri`·`metadata`를 비운 응답을 준다(`_redact_for_labeler`).

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            멤버가 아닌 경우(403 `not_a_member`).
    """
    video = await db.get(Video, video_id)
    if not video or video.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await _require_member(db, video.project_id, user)
    thumbs = await _first_clip_thumbnails(db, [video.video_id])
    thumb = thumbs.get(video.video_id) or _poster_url(video)
    lib = (await _library_status(db, [video.video_id])).get(video.video_id)
    dto = _to_dto(video, thumbnail_url=thumb, lib=lib)
    if not await authz.is_work_result_viewer(db, video.project_id, user):
        return _redact_for_labeler(dto)
    return dto



async def _may_play_original(db: AsyncSession, video: Video, user: User) -> bool:
    """호출한 사용자에게 원본을 재생 매체로 줄 수 있으면 참이다.

    비식별화를 쓰는 영상의 원본은 마스킹 전 파일이다. 재생본이 없을 때 원본으로 대체하면
    가리지 않은 얼굴이 라벨러 화면에 나온다. 그래서 비식별화본이 필요한 영상의 원본은
    작업 결과를 볼 수 있는 역할(관리자·검수자)에게만 준다.
    비식별화본이 필요한지는 썸네일과 같은 규칙(`thumbnail_source.masked_required`)으로 판정한다.
    """
    from api_gateway.services.media import thumbnail_source

    masked = await db.scalar(
        select(thumbnail_source.masked_required()).where(Video.video_id == video.video_id))
    if not masked:
        return True
    return await authz.is_work_result_viewer(db, video.project_id, user)


async def issue_source_url(db: AsyncSession, video_id: UUID, user: User) -> dict:
    """프로젝트 멤버에게 영상 재생용 presigned GET URL을 발급한다(Vidstack용).

    재생 매체는 `resolver.playback_source`가 고른다(480p 재생본 우선, 없으면 원본).
    비식별화본이 필요한 영상은 라벨러에게 원본을 주지 않는다(`_may_play_original`).
    고정 키를 가정하지 않고 그 결과의 실제 bucket/key로 서명한다.

    Raises:
        HTTPException: 영상이 없거나 삭제된 경우(404 `video_not_found`), 멤버가 아닌 경우(403),
            업로드가 끝나지 않은 경우(409 `video_not_uploaded`),
            호출한 사용자에게 줄 수 있는 재생 사본이 없는 경우(409 `invalid_source_uri`).
    """
    video = await db.get(Video, video_id)
    if not video or video.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await _require_member(db, video.project_id, user)
    # 오류 코드가 화면 문장이 되므로 두 조건을 다른 코드로 낸다.
    # 업로드는 끝났는데 원본 위치가 없으면 재생할 사본이 없는 경우라서
    # 아래 `playback_source`가 없을 때와 같은 `invalid_source_uri`를 낸다.
    # `video_not_uploaded`로 묶으면 업로드를 마친 영상에 업로드 미완료 문구가 표시된다.
    if video.upload_status != "uploaded":
        raise HTTPException(status.HTTP_409_CONFLICT, "video_not_uploaded")
    if not video.source_uri:
        raise HTTPException(status.HTTP_409_CONFLICT, "invalid_source_uri")

    # 재생 매체와 존재하는 사본만 가리키는 규칙은 `resolver` 한 곳에 있다.
    # 480p 재생본을 우선하고(원본보다 대역폭이 약 59% 적다), 없으면 원본을 쓴다.
    allow_original = await _may_play_original(db, video, user)
    src = resolver.playback_source(video, allow_original=allow_original)
    if src is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "invalid_source_uri")
    await storage.ensure_loaded(db, video.extra)
    return {
        "url": storage.presign_get_for(bucket=src.bucket, key=src.key),
        "expires_in": settings.s3_presign_ttl_seconds,
        "variant": src.variant,
    }
