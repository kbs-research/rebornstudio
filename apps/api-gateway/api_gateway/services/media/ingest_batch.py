"""반입 묶음(ingest_batches)의 생성 보장과 목록 집계.

`videos.upload_batch_id`는 클라이언트가 `crypto.randomUUID()`로 만들어 영상마다 붙이는 값이다.
묶음마다 행을 두어 이름·출처·수행자를 기록한다. 그래서 반입 직후 받은 URL을 잃어도
지난 반입의 내용과 양을 목록에서 다시 찾을 수 있다.

주요 함수는 다음과 같다.
  1. `ensure_batch`: 묶음의 첫 영상을 받을 때 행을 만든다. 클라이언트가 보낸 id를 그대로 쓰고,
     보내지 않으면 서버가 만든다.
  2. `list_for_project`: 묶음별 반입 상태 분포를 목록과 같은 SQL CASE로 집계한다.
  3. `get_detail`·`update`: 등록 Job 한 건의 상세 조회와 이름·메모 수정을 한다.
"""
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from reborn_contracts import (
    IngestBatchDTO,
    IngestBatchUpdate,
    IngestJobDetailDTO,
    IngestJobIssue,
    PrepStageCounts,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Clip, IngestBatch, User, Video
from api_gateway.services import audit, authz

# 목록 한 페이지의 기본 행 수. 묶음 수는 반입 빈도에 따라 계속 늘어나므로
# 목록은 최신순 커서로 페이지를 나눈다.
_DEFAULT_LIMIT = 50


async def ensure_batch(
    db: AsyncSession, *, project_id: UUID, batch_id: UUID | None,
    user: User, source: str = "upload",
) -> UUID:
    """묶음 행이 없으면 만들고 그 id를 돌려준다. 두 번째 영상부터는 행이 있어 아무것도 하지 않는다.

    클라이언트가 `batch_id`를 보내지 않으면 여기서 만든다. 영상마다 Job이 생기는 것이 계약이라
    API를 직접 부르는 경로(스크립트·워커·다른 클라이언트)로 올린 영상에도 Job이 생긴다.

    대량 반입은 영상마다 이 함수를 부르고 동시 업로드도 있으므로 ON CONFLICT DO NOTHING을 쓴다.
    커밋은 호출자(영상 생성 트랜잭션)가 하므로 영상이 생기지 않으면 묶음 행도 남지 않는다.

    다른 프로젝트의 묶음 id는 받지 않는다. 행이 이미 있으면 삽입을 건너뛰므로, 프로젝트를
    확인하지 않으면 영상이 다른 프로젝트의 묶음에 붙어 그 묶음의 상세와 메타 양식에 실린다.
    다른 프로젝트 묶음이 있는지 드러내지 않도록 없는 묶음과 같은 404로 응답한다.

    Returns:
        UUID: 행이 있는 묶음 id. 호출자는 넘긴 값이 아니라 이 값을 영상에 붙여야 한다.

    Raises:
        HTTPException: 다른 프로젝트의 묶음인 경우(404 `batch_not_found`).
    """
    batch_id = batch_id or uuid4()
    await db.execute(
        pg_insert(IngestBatch)
        .values(
            batch_id=batch_id, project_id=project_id,
            created_by=user.user_id, source=source,
        )
        .on_conflict_do_nothing(index_elements=[IngestBatch.batch_id])
    )
    owner = await db.scalar(
        select(IngestBatch.project_id).where(IngestBatch.batch_id == batch_id)
    )
    if owner != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch_not_found")
    return batch_id


async def list_for_project(
    db: AsyncSession, project_id: UUID, user: User, *,
    limit: int = _DEFAULT_LIMIT,
    before: str | None = None,
    before_id: UUID | None = None,
) -> list[IngestBatchDTO]:
    """묶음 목록을 최신순 커서 페이지로 돌려준다.

    각 행에 반입 상태 분포와 총 길이를 붙인다.
    분포는 목록 필터·배지와 같은 SQL(`_ingest_bucket_case`)로 센다.
    여기서 따로 세면 "묶음에서는 3건 막힘인데 목록 필터에서는 2건"처럼 어긋난다.
    """
    await authz.require_project_admin(db, project_id, user)
    # `video` 모듈이 이 모듈을 import하므로 판정 규칙(CASE)은 함수 안에서 import한다.
    from api_gateway.services.media.video import _ingest_bucket_case, _prep_stage_case

    bucket = _ingest_bucket_case()
    per_video = (
        select(
            Video.upload_batch_id.label("bid"),
            Video.duration_seconds.label("dur"),
            bucket.label("bucket"),
            _prep_stage_case().label("prep"),
        )
        .where(
            Video.project_id == project_id,
            Video.upload_batch_id.is_not(None),
            Video.deleted_at.is_(None),
        )
        .subquery()
    )

    def _n(name: str):
        """반입 상태 버킷 하나에 속한 영상 수를 센다."""
        return func.count().filter(per_video.c.bucket == name)

    def _p(name: str):
        """준비 단계 하나에 속한 영상 수를 센다. 화면의 묶음 세그먼트 바가 쓴다."""
        return func.count().filter(per_video.c.prep == name)

    agg = (
        select(
            per_video.c.bid,
            func.count().label("videos"),
            func.coalesce(func.sum(per_video.c.dur), 0.0).label("dur"),
            _n("ready").label("ready"),
            _n("analyzing").label("analyzing"), _n("done").label("done"),
            _n("blocked").label("blocked"),
            _p("uploading").label("p_uploading"), _p("probing").label("p_probing"),
            _p("transcoding").label("p_transcoding"),
            _p("awaiting_analysis").label("p_awaiting"),
            _p("registering").label("p_registering"), _p("analyzing").label("p_analyzing"),
            _p("summarizing").label("p_summarizing"),
            _p("ready_for_work").label("p_ready"), _p("blocked").label("p_blocked"),
        )
        .group_by(per_video.c.bid)
        .subquery()
    )
    # 삭제된 영상 수. 위 분포는 삭제되지 않은 영상만 센다.
    # 이 값이 없으면 모두 삭제된 묶음이 '0건 · 반입 완료'로 표시된다.
    deleted = (
        select(func.count()).select_from(Video)
        .where(
            Video.upload_batch_id == IngestBatch.batch_id,
            Video.deleted_at.is_not(None),
        ).correlate(IngestBatch).scalar_subquery()
    )

    q = (
        select(IngestBatch, agg, deleted.label("deleted_videos"))
        .outerjoin(agg, agg.c.bid == IngestBatch.batch_id)
        .where(IngestBatch.project_id == project_id)
        .order_by(IngestBatch.created_at.desc(), IngestBatch.batch_id.desc())
        .limit(limit)
    )
    # 커서는 (created_at, batch_id) 복합 키라서 같은 초에 여러 묶음이 생겨도 빠지거나 겹치지 않는다.
    if before and before_id:
        q = q.where(
            (IngestBatch.created_at, IngestBatch.batch_id) < (before, before_id)
        )

    out: list[IngestBatchDTO] = []
    for row in (await db.execute(q)).all():
        b = row[0]  # IngestBatch. 나머지는 라벨로 읽는다(agg 서브쿼리 열, deleted_videos).
        out.append(IngestBatchDTO(
            batch_id=b.batch_id, project_id=b.project_id, source=b.source,
            label=b.label, note=b.note, created_by=b.created_by, created_at=b.created_at,
            import_prefix=(b.import_spec or {}).get("prefix"),
            import_status=b.import_status, import_error=b.import_error,
            import_skipped_existing=int((b.import_spec or {}).get("skipped_existing", 0)),
            video_count=int(row.videos or 0),
            total_duration_seconds=float(row.dur or 0.0),
            ready=int(row.ready or 0),
            analyzing=int(row.analyzing or 0), done=int(row.done or 0),
            blocked=int(row.blocked or 0),
            prep=PrepStageCounts(
                uploading=int(row.p_uploading or 0), probing=int(row.p_probing or 0),
                transcoding=int(row.p_transcoding or 0),
                awaiting_analysis=int(row.p_awaiting or 0),
                registering=int(row.p_registering or 0), analyzing=int(row.p_analyzing or 0),
                summarizing=int(row.p_summarizing or 0),
                ready_for_work=int(row.p_ready or 0), blocked=int(row.p_blocked or 0),
                total=int(row.videos or 0),
            ),
            deleted_videos=int(row.deleted_videos or 0),
        ))
    return out


async def get_detail(
    db: AsyncSession, batch_id: UUID, user: User
) -> IngestJobDetailDTO:
    """등록 Job 한 건의 상세를 조회한다. 목록보다 많은 정보를 담는다.

    목록 DTO는 한 번에 50행이라 조회 비용을 줄이려고 필드를 적게 둔다.
    상세는 1건이므로 수행자 이름, 이슈의 단계별 분포, 클립 수까지 채운다.

    Raises:
        HTTPException: 묶음이 없는 경우(404 `batch_not_found`),
            관리자가 아닌 경우(403).
    """
    from api_gateway.services.media.video import (
        _prep_blocked_stage_case,
        _prep_stage_case,
    )

    batch = await db.get(IngestBatch, batch_id)
    if not batch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch_not_found")
    await authz.require_project_admin(db, batch.project_id, user)

    # 묶음 프로젝트의 영상만 센다.
    # 기존 데이터에는 다른 프로젝트 영상이 같은 묶음 id를 달고 있을 수 있다.
    scope = [Video.upload_batch_id == batch_id, Video.project_id == batch.project_id,
             Video.deleted_at.is_(None)]

    # 준비 단계 분포. 화면마다 수가 어긋나지 않게 목록·칩과 같은 CASE를 쓴다.
    rows = (await db.execute(
        select(_prep_stage_case().label("prep"), func.count())
        .where(*scope).group_by("prep")
    )).all()
    counts = dict.fromkeys((
        "uploading", "probing", "transcoding", "awaiting_analysis",
        "registering", "analyzing", "summarizing", "ready_for_work", "blocked",
    ), 0)
    for name, n in rows:
        counts[name] = int(n)
    prep = PrepStageCounts(**counts, total=sum(counts.values()))

    # 이슈를 막힌 단계별로 나눈다.
    # "이슈 4건"보다 "검증 3 · 변환 1"이어야 무엇부터 볼지 정할 수 있다.
    issue_rows = (await db.execute(
        select(_prep_blocked_stage_case().label("at"), func.count())
        .where(*scope, _prep_stage_case() == "blocked")
        .group_by("at").order_by(func.count().desc())
    )).all()
    issues = [IngestJobIssue(stage=at, count=int(n)) for at, n in issue_rows]

    # 규모와 삭제된 수를 센다.
    # 삭제되지 않은 영상만 세면 모두 삭제된 Job이 '0건 완료'로 표시된다.
    size = (await db.execute(
        select(func.count(), func.coalesce(func.sum(Video.duration_seconds), 0.0))
        .where(*scope)
    )).one()
    deleted = int(await db.scalar(
        select(func.count()).select_from(Video)
        .where(Video.upload_batch_id == batch_id, Video.project_id == batch.project_id,
               Video.deleted_at.is_not(None))
    ) or 0)

    # 이 Job의 영상에서 나온 클립 수를 센다.
    clip_count = int(await db.scalar(
        select(func.count()).select_from(Clip)
        .join(Video, Video.video_id == Clip.video_id).where(*scope)
    ) or 0)

    actor = (
        await db.scalar(select(User.display_name).where(User.user_id == batch.created_by))
        if batch.created_by else None
    )

    return IngestJobDetailDTO(
        batch_id=batch.batch_id, project_id=batch.project_id, source=batch.source,
        label=batch.label, note=batch.note, created_at=batch.created_at,
        created_by=batch.created_by, created_by_name=actor,
        import_prefix=(batch.import_spec or {}).get("prefix"),
        import_status=batch.import_status, import_error=batch.import_error,
        import_skipped_existing=int((batch.import_spec or {}).get("skipped_existing", 0)),
        video_count=int(size[0] or 0), deleted_videos=deleted,
        total_duration_seconds=float(size[1] or 0.0),
        prep=prep, issues=issues,
        clip_count=clip_count,
    )


async def update(
    db: AsyncSession, batch_id: UUID, payload: IngestBatchUpdate, user: User
) -> IngestBatchDTO:
    """묶음 이름과 메모를 고친다. 나중에 어떤 반입이었는지 알아보기 위한 값이다.

    Raises:
        HTTPException: 묶음이 없는 경우(404 `batch_not_found`),
            관리자가 아닌 경우(403).
    """
    batch = await db.get(IngestBatch, batch_id)
    if not batch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch_not_found")
    await authz.require_project_admin(db, batch.project_id, user)
    if payload.label is not None:
        batch.label = payload.label.strip() or None
    if payload.note is not None:
        batch.note = payload.note.strip() or None
    audit.emit(
        db, event_type="ingest_batch.updated", target_kind="ingest_batch",
        target_id=batch.batch_id, actor_user_id=user.user_id, project_id=batch.project_id,
        payload={"label": batch.label},
    )
    await db.commit()
    await db.refresh(batch)
    rows = await list_for_project(db, batch.project_id, user, limit=_DEFAULT_LIMIT)
    return next((r for r in rows if r.batch_id == batch.batch_id), IngestBatchDTO(
        batch_id=batch.batch_id, project_id=batch.project_id, source=batch.source,
        label=batch.label, note=batch.note, created_by=batch.created_by,
        created_at=batch.created_at,
        import_prefix=(batch.import_spec or {}).get("prefix"),
        import_status=batch.import_status, import_error=batch.import_error,
        import_skipped_existing=int((batch.import_spec or {}).get("skipped_existing", 0)),
    ))
