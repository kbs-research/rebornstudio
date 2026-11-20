"""클립 조회·생성(분석 결과·수동 입력) 서비스."""
import math
import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import (
    ClipCountDTO,
    ClipCreateRequest,
    ClipDTO,
    VideoMetaDTO,
)
from sqlalchemy import cast, func, or_, select, tuple_
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Clip, Project, User, Video, WorkItem
from api_gateway.services import audit, authz, text_search
from api_gateway.services import video_meta as inherited
from api_gateway.services.analysis import scene_options
from api_gateway.services.media import progress, storage

# 한글 음절 패턴. 실패 사유가 서버 코드가 만든 문구인지 판별한다.
_HANGUL = re.compile(r"[가-힣]")


def thumbnail_reason(raw: object) -> str | None:
    """재시도를 멈춘 썸네일의 실패 사유를 화면 문구로 바꾼다.

    클립과 포스터가 함께 쓴다. 한글이 있으면 서버 코드가 만든 사유라서 앞 200자를 그대로 싣는다.
    영문이면 ffmpeg 원문 줄이라서 "프레임을 추출하지 못했습니다"로 바꾼다. 원문은 `extra`에 남는다.
    `error_text.operator_text`는 원문이 서버 기록에 있다고 안내한다.
    썸네일 원문은 감사 기록 대신 `extra`에 남으므로 그 함수를 쓰지 않는다.

    Returns:
        str | None: 화면 사유. 입력이 비었으면 `None`.
    """
    text = str(raw or "").removeprefix("[일시]").strip()
    if not text:
        return None
    return text[:200] if _HANGUL.search(text) else "프레임을 추출하지 못했습니다"


def _thumbnail_url(meta: dict) -> str | None:
    """클립 썸네일 URL을 정한다. 앱 버킷 키가 있으면 요청마다 새로 서명한다.

    `scene_thumbnail_key`는 영상에서 추출해 `scene_thumbnail_bucket`에 올린 프레임의 키다.
    버킷 기록이 없으면 `None`을 돌려준다. 응답마다 새로 서명하므로 URL이 만료되지 않는다.
    키가 없으면 레거시 `scene_thumbnail_url`(만료될 수 있는 MAIU presigned URL)을 쓴다.
    화면은 이미지 로드에 실패하면 placeholder를 표시한다.
    """
    key = meta.get("scene_thumbnail_key")
    if key:
        return storage.presign_get_or_none(key, bucket=meta.get("scene_thumbnail_bucket"))
    return meta.get("scene_thumbnail_url")


def _to_dto(
    c: Clip,
    video_title: str | None = None,
    work: tuple[str, UUID] | None = None,
    video_meta: VideoMetaDTO | None = None,
) -> ClipDTO:
    """Clip ORM을 DTO로 바꾼다.

    scene·keywords·thumbnail은 `extra`에서, scene_summary는 `auto_caption.summary`
    (장면 요약 VLM)에서 읽는다.
    `video_title`은 프로젝트 목록(`list_for_project`)이 조인으로 채우고,
    단건 조회(`get_one`)는 영상을 읽어 채운다.

    Args:
        work: 라벨링 티켓의 `(status, work_item_id)`. 티켓이 없으면 `None`을 그대로 둔다.
            `available`로 채우면 티켓이 없는 클립(비식별화 신고, 제외)이 "라벨링 대기"로 표시된다.
        video_meta: 이 클립이 물려받는 영상 메타 네 가지. 단건 조회만 싣는다.
    """
    cap = c.auto_caption or {}
    speakers = c.speakers or []
    meta = c.extra or {}
    return ClipDTO(
        clip_id=c.clip_id,
        video_id=c.video_id,
        project_id=c.project_id,
        video_title=video_title,
        video_meta=video_meta,
        start_seconds=float(c.start_seconds),
        end_seconds=float(c.end_seconds),
        rights_status=c.rights_status,
        auto_caption_brief=cap.get("brief"),
        scene_summary=cap.get("summary"),
        scene=meta.get("scene"),
        speakers=speakers,
        keywords=meta.get("keywords") or [],
        speeches=cap.get("speeches") or [],
        scene_thumbnail_url=_thumbnail_url(meta),
        # 재시도를 멈춘 실패만 싣는다.
        # 재시도 중인 실패까지 실으면 곧 생길 썸네일이 화면에 실패로 표시된다.
        # 사유는 ffmpeg 마지막 줄일 수 있어 화면 문구로 바꾼다(`thumbnail_reason`).
        thumbnail_error=(
            thumbnail_reason(meta.get("thumbnail_error")) or "썸네일을 만들지 못했습니다"
            if meta.get("thumbnail_failed") == "true" else None
        ),
        source=meta.get("source"),
        work_status=work[0] if work else None,
        work_item_id=work[1] if work else None,
        progress=progress.clip_progress(c.rights_status, work[0] if work else None),
        created_at=c.created_at,
    )


# 씬 클립의 초기 rights_status. 클립을 만들자마자 정상 상태로 둔다.
# `work.service`는 cleared 계열 클립에만 work_item을 만들므로 분석 직후 라벨링 작업이 생긴다.
# 정상 상태에서 벗어나는 경로는 비식별화 미흡 신고(`legal_review`)와
# 신고의 제외 처리(`rejected`)뿐이다.
_SCENE_RIGHTS_INITIAL_DEFAULT = "auto_cleared"


def fit_to_video(
    start_seconds: float, end_seconds: float, duration_seconds: float | None,
) -> tuple[float, float] | None:
    """구간의 끝을 영상 길이 안으로 줄인다. 영상 길이를 모르면 구간을 그대로 돌려준다.

    분석기가 영상보다 긴 구간을 돌려주면 클립이 없는 프레임을 가리키고 납품 초가 부풀려진다.
    길이를 모르는 것과 길이를 넘은 것은 다르므로 길이가 없으면 줄이지 않는다.
    클립 시각 열은 소수 셋째 자리까지 저장하므로 영상 길이를 밀리초 단위로 내림한다.
    반올림하면 저장한 끝이 영상 길이보다 1밀리초 길어질 수 있다.

    Returns:
        tuple[float, float] | None: 줄인 `(시작, 끝)`.
            시작이 영상 끝 이후라 남는 구간이 없으면 `None`.
    """
    dur = math.floor(round(float(duration_seconds or 0) * 1000, 6)) / 1000
    if not dur or end_seconds <= dur:
        return start_seconds, end_seconds
    if start_seconds >= dur:
        return None
    return start_seconds, dur


async def create_scene_clip(
    db: AsyncSession,
    *,
    video: Video,
    start_seconds: float,
    end_seconds: float,
    auto_caption: dict,
    speakers: list[dict],
    scene_extra: dict,
) -> bool:
    """씬 클립 1건을 만든다. Clip INSERT는 이 함수만 한다.

    생산자(maiu reconciler, 씬 분할, 수동 입력)는 모두 이 함수를 거친다.
    초기 rights_status 규칙(`_SCENE_RIGHTS_INITIAL_DEFAULT`)도 생성 규칙이라 여기에 둔다.

    끝이 영상 길이를 넘는 구간은 저장 직전인 여기서 영상 끝까지로 줄인다(`fit_to_video`).
    생산자마다 줄이면 한 곳이 빠졌을 때 영상 밖 클립이 저장된다.
    줄인 구간이 프로젝트 클립 길이 하한(`scene_options.clip_min_seconds`)보다 짧으면 만들지 않는다.
    하한은 줄어든 구간에만 적용한다. 분석기가 처음부터 짧게 자른 구간은 그대로 만든다.

    Returns:
        bool: 만들었으면 True. 중복이거나 영상 끝 이후 구간이면 False.
            UNIQUE (video, start, end) 위반(동시 실행 경합)은 savepoint로 흡수해
            배치 전체가 롤백되지 않게 한다.
    """
    from sqlalchemy.exc import IntegrityError

    fitted = fit_to_video(start_seconds, end_seconds, video.duration_seconds)
    if fitted is None:
        return False
    if fitted[1] != end_seconds:
        floor = await _min_seconds(db, video.project_id)
        if fitted[1] - fitted[0] < floor:
            return False
    start_seconds, end_seconds = fitted
    try:
        async with db.begin_nested():
            db.add(Clip(
                video_id=video.video_id,
                project_id=video.project_id,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                auto_caption=auto_caption,
                speakers=speakers,
                rights_status=_SCENE_RIGHTS_INITIAL_DEFAULT,
                extra=scene_extra,
            ))
    except IntegrityError:
        return False
    return True


async def create_manual(
    db: AsyncSession, project_id: UUID, video_id: UUID,
    payload: ClipCreateRequest, user: User,
) -> ClipDTO:
    """사람이 입력한 씬 구간으로 클립을 만든다.

    분석 기능이 없는 스택에서는 씬을 만드는 유일한 방법이다.
    초기 클립 상태 규칙을 한 곳에 두려고 `create_scene_clip`을 거친다.
    분석 서비스가 자른 클립과 구분하려고 출처를 `extra.source = "manual"`로 남긴다.

    만든 클립에 라벨링 작업을 열고(`work.service.ensure_for_clip`) 응답에 작업의 상태와 id를 싣는다.
    영상 단위 분석 요청 티켓도 같은 트랜잭션에서 지운다(`work.service.retire_video_ticket`).
    티켓이 남으면 프로젝트 전체 분석 요청이 클립이 있는 영상을 MI에 보내 과금하고
    사람이 넣은 구간과 겹치는 클립을 만든다.

    구간은 프로젝트 클립 길이 하한(`scene_options.clip_min_seconds`) 이상이어야 한다.
    더 짧은 구간은 라벨러가 볼 수 없는 조각이 되고, 만든 뒤에는 지울 방법이 없다.

    기존 클립과 겹치는 구간은 만들지 않는다. 겹친 두 클립은 같은 장면을 두 번 라벨링하게 한다.
    한쪽이 비식별화 신고로 제외되어도 다른 쪽 클립으로 같은 내용이 작업자에게 나간다.
    앞 클립의 끝과 새 구간의 시작이 같으면 맞닿은 것이라 허용한다.
    검사와 삽입 사이에 다른 요청이 끼지 않도록 영상 행을 잠근다(`_lock_video`).

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            같은 구간이 있거나 기존 클립과 겹치는 경우(409 `clip_overlaps`),
            영상 길이를 0.5초 넘게 넘는 경우(400 `out_of_range`),
            프로젝트 하한보다 짧은 경우(422 `clip_too_short`).
    """
    video = await db.get(Video, video_id)
    if not video or video.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_project_admin(db, project_id, user)
    floor = await _min_seconds(db, project_id)
    if payload.end_seconds - payload.start_seconds < floor:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "clip_too_short")
    await _lock_video(db, video_id)

    # 영상 길이를 넘는 구간은 거절한다. 만들면 열 수 없는 클립이 된다.
    # 길이 정보가 없으면 통과시킨다. 길이를 알 수 없는 것과 길이를 넘은 것은 다르다.
    dur = float(video.duration_seconds or 0)
    if dur and payload.end_seconds > dur + 0.5:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "out_of_range")
    # 허용 오차 안에서 길이를 넘은 끝은 `create_scene_clip`처럼 영상 끝으로 줄인다.
    # 줄인 값으로 하한을 다시 확인하지 않으면 저장 단계가 구간을 건너뛰어 겹침 오류로 잘못 응답한다.
    fitted = fit_to_video(payload.start_seconds, payload.end_seconds, dur)
    if fitted is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "out_of_range")
    start, end = fitted
    if end - start < floor:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "clip_too_short")
    overlap = await db.scalar(
        select(Clip.clip_id).where(
            Clip.video_id == video_id,
            Clip.start_seconds < end,
            Clip.end_seconds > start,
        ).limit(1)
    )
    if overlap is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "clip_overlaps")

    made = await create_scene_clip(
        db, video=video, start_seconds=start, end_seconds=end,
        # 캡션은 라벨링이 채운다. 여기서 넣으면 생성 결과와 작업 결과가 섞인다.
        auto_caption={}, speakers=[], scene_extra={"source": "manual"},
    )
    if not made:
        # UNIQUE (video, start, end) 위반. 같은 구간이 이미 있다.
        raise HTTPException(status.HTTP_409_CONFLICT, "clip_overlaps")

    row = (await db.scalars(
        select(Clip).where(
            Clip.video_id == video_id,
            Clip.start_seconds == start,
            Clip.end_seconds == end,
        )
    )).one()
    # 클립과 라벨링 작업을 한 트랜잭션에 쓴다. 작업 없이 커밋된 클립은 대기열에 나타나지 않는다.
    # 작업을 여는 조건은 장면 분할·분석 회수와 같이 `ensure_for_clip`이 판정한다.
    from api_gateway.services.work import service as work_service

    item = await work_service.ensure_for_clip(db, row)
    # 클립이 생긴 영상의 분석 요청 티켓을 같은 트랜잭션에서 지운다.
    # 따로 지우면 커밋과 정리 사이에 분석 요청이 클립 있는 영상을 가져갈 수 있다.
    await work_service.retire_video_ticket(db, video_id)
    audit.emit(db, event_type="clip.created_manually", target_kind="clip",
               target_id=row.clip_id, project_id=project_id,
               actor_user_id=user.user_id,
               payload={"start": start, "end": end})
    await db.commit()
    return _to_dto(row, work=(item.status, item.work_item_id) if item is not None else None)


async def _lock_video(db: AsyncSession, video_id: UUID) -> None:
    """영상 행을 잠가 같은 영상의 구간 추가를 직렬화한다. 트랜잭션이 끝나면 풀린다.

    두 요청이 이웃 클립을 동시에 읽으면 둘 다 겹치지 않는다고 판단해 겹친 두 구간을 쓴다.
    아직 없는 클립은 클립 행 잠금으로 막을 수 없어서 영상 행을 잠근다.
    """
    await db.execute(select(Video.video_id).where(Video.video_id == video_id).with_for_update())


async def _min_seconds(db: AsyncSession, project_id: UUID) -> int:
    """프로젝트에서 사람이 넣는 구간의 최소 길이(초)를 읽는다."""
    settings_ = await db.scalar(select(Project.settings).where(Project.project_id == project_id))
    return scene_options.clip_min_seconds(settings_)


async def list_for_video(
    db: AsyncSession, project_id: UUID, video_id: UUID, user: User
) -> list[ClipDTO]:
    """영상의 클립을 start_seconds 오름차순으로 돌려준다. 프로젝트 멤버만 볼 수 있다.

    라벨러(annotator)도 가져간 클립의 시간 범위·자막·화자를 봐야 하므로 멤버에게 연다.
    라벨러에게는 정상 상태의 클립만 주고 상태 구분과 다른 사람의 작업 상태를 가린다.
    가림 규칙은 `progress.clip_audience`가 정한다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            멤버가 아닌 경우(403 `not_a_member`).
    """
    video = await db.get(Video, video_id)
    if not video or video.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_member(db, project_id, user)
    rows = list(await db.scalars(
        select(Clip)
        .where(Clip.video_id == video_id)
        .order_by(Clip.start_seconds.asc())
    ))
    work = await _work_map(db, [c.clip_id for c in rows])
    dtos = [_to_dto(c, work=work.get(c.clip_id)) for c in rows]
    return await _for_audience(db, project_id, user, dtos)


async def _for_audience(
    db: AsyncSession, project_id: UUID, user: User, dtos: list[ClipDTO],
) -> list[ClipDTO]:
    """호출한 사용자의 역할에 맞게 클립 목록을 가린다. 두 목록 조회가 같은 규칙을 쓴다."""
    audience = await progress.clip_audience(db, project_id, user)
    if audience == "labeler":
        mine = await progress.my_tickets(db, [d.clip_id for d in dtos], user)
        return progress.redact_clips_for_labeler(dtos, mine)
    return dtos


async def _work_map(db: AsyncSession, clip_ids: list[UUID]) -> dict[UUID, tuple[str, UUID]]:
    """클립별 `(라벨링 상태, work_item_id)`를 돌려준다. 티켓이 없는 클립은 키가 없다.

    `work_items`는 조회만 한다. 쓰기와 상태 전이는 `work`가 소유한다.
    클립당 티켓이 여럿이면 가장 최근 티켓을 쓴다. 임의로 고르면 새로고침마다 다른 상태가 표시된다.
    """
    if not clip_ids:
        return {}
    rows = (await db.execute(
        select(WorkItem.clip_id, WorkItem.status, WorkItem.work_item_id)
        .where(WorkItem.clip_id.in_(clip_ids))
        .order_by(WorkItem.created_at)
    )).all()
    return {cid: (st, wid) for cid, st, wid in rows}


async def get_one(
    db: AsyncSession, project_id: UUID, clip_id: UUID, user: User
) -> ClipDTO:
    """클립 1건을 조회한다. 프로젝트 멤버만 볼 수 있다.

    라벨링 화면(work/label)이 쓴다.
    클립이 물려받는 영상 메타 네 가지(방송사·프로그램명·장르·방영일)를 함께 싣는다.
    라벨링·검수 화면이 이 값을 읽기 전용으로 표시한다.
    클립 응답에 실어 라벨러에게 영상 API를 열지 않는다.
    라벨러가 신고 중이거나 제외된 클립을 요청하면 목록과 같은 규칙으로 404를 돌려준다.

    Raises:
        HTTPException: 클립이 없거나 라벨러가 볼 수 없는 클립인 경우(404 `clip_not_found`),
            멤버가 아닌 경우(403 `not_a_member`).
    """
    clip = await db.get(Clip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "clip_not_found")
    await authz.require_member(db, project_id, user)
    video = await db.get(Video, clip.video_id)
    work = await _work_map(db, [clip_id])
    dto = _to_dto(
        clip,
        video_title=video.title if video else None,
        work=work.get(clip_id),
        video_meta=inherited.of_video(video),
    )
    audience = await progress.clip_audience(db, project_id, user)
    if audience == "labeler":
        mine = await progress.my_tickets(db, [clip_id], user)
        shown = progress.redact_clip_for_labeler(dto, mine)
        if shown is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "clip_not_found")
        return shown
    return dto


async def list_for_project(
    db: AsyncSession, project_id: UUID, user: User, *,
    q_text: str | None = None,
    limit: int = 50,
    before_at: datetime | None = None,
    before_video_id: UUID | None = None,
    before_start: float | None = None,
    before_id: UUID | None = None,
) -> list[ClipDTO]:
    """프로젝트 전체 클립을 video_title과 함께 돌려준다. 멤버가 볼 수 있다.

    콘텐츠 화면의 클립 탭이 쓴다. 읽기 전용이라 검수자도 볼 수 있다.
    라벨러에게 보이지 않는 클립 상태는 SQL에서 먼저 거른다(`progress.visible_rights`).
    페이지를 자른 뒤에 거르면 한 페이지가 덜 차서 화면이 마지막 페이지로 잘못 처리한다.

    정렬 순서는 생성 시각 최신순, 영상, 시작 시각 순이다.
    한 번의 분석으로 만든 클립은 같은 트랜잭션이라 `created_at`이 같다.
    (created_at, clip_id)로만 정렬하면 한 영상의 클립이 무작위 id 순으로 섞인다.
    커서는 직전 페이지 마지막 항목의 (created_at, video_id, start_seconds, clip_id)다.
    행 비교는 모든 열을 같은 방향으로 비교하므로 시작 시각만 부호를 뒤집어 오름차순을 표현한다.

    Args:
        q_text: 영상 제목·장면 요약 부분 일치 검색어(대소문자 무시). 페이지 밖의 클립도
            찾도록 서버에서 거른다. `%`·`_`도 글자로 찾는다(`text_search.contains`).

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 `project_not_found`),
            멤버가 아닌 경우(403 `not_a_member`),
            커서 넷 중 일부만 온 경우(422 `incomplete_cursor`).
            일부만 비교하면 행을 건너뛰거나 중복해서 돌려준다.
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_member(db, project_id, user)
    cursor = (before_at, before_video_id, before_start, before_id)
    if any(c is not None for c in cursor) and not all(c is not None for c in cursor):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "incomplete_cursor")
    stmt = (
        select(Clip, Video.title)
        .join(Video, Video.video_id == Clip.video_id)
        .where(*await _project_clip_filters(db, project_id, user, q_text))
    )
    if before_at is not None:
        stmt = stmt.where(
            tuple_(Clip.created_at, Clip.video_id, -Clip.start_seconds, Clip.clip_id)
            < tuple_(before_at, before_video_id, -Decimal(str(before_start)), before_id)
        )
    stmt = stmt.order_by(
        Clip.created_at.desc(), Clip.video_id.desc(), Clip.start_seconds.asc(), Clip.clip_id.desc(),
    ).limit(limit)
    rows = (await db.execute(stmt)).all()
    # 콘텐츠 클립 표의 작업 열이 쓰는 작업 상태를 페이지의 클립 id로 한 번에 조회한다.
    work = await _work_map(db, [c.clip_id for c, _ in rows])
    dtos = [_to_dto(c, video_title=title, work=work.get(c.clip_id)) for c, title in rows]
    return await _for_audience(db, project_id, user, dtos)




async def _project_clip_filters(
    db: AsyncSession, project_id: UUID, user: User, q_text: str | None,
) -> list:
    """프로젝트 클립 목록과 수 세기가 같이 쓰는 조건을 만든다. `Video` 조인을 전제로 한다.

    목록과 분모가 다른 조건을 쓰면 "N개 중"의 N이 목록과 어긋난다.
    역할에 따라 보이지 않는 클립 상태도 여기서 거른다(`progress.visible_rights`).
    """
    conds: list = [Clip.project_id == project_id]
    like = text_search.contains(q_text)
    if like:
        conds.append(or_(
            Video.title.ilike(like, escape=text_search.ESCAPE),
            Clip.auto_caption["summary"].astext.ilike(like, escape=text_search.ESCAPE),
        ))
    shown_rights = progress.visible_rights(await progress.clip_audience(db, project_id, user))
    if shown_rights is not None:
        conds.append(Clip.rights_status.in_(shown_rights))
    return conds


async def count_for_project(
    db: AsyncSession, project_id: UUID, user: User, *,
    q_text: str | None = None,
) -> ClipCountDTO:
    """프로젝트 클립 목록(`list_for_project`)과 같은 조건으로 클립 수를 센다. 멤버가 볼 수 있다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 `project_not_found`),
            멤버가 아닌 경우(403 `not_a_member`).
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_member(db, project_id, user)
    total = await db.scalar(
        select(func.count()).select_from(Clip)
        .join(Video, Video.video_id == Clip.video_id)
        .where(*await _project_clip_filters(db, project_id, user, q_text))
    )
    return ClipCountDTO(total=int(total or 0))


async def merge_extra(
    db: AsyncSession, clip_id: UUID, patch: dict, *, remove: tuple[str, ...] = ()
) -> None:
    """`clips.metadata`(JSONB)를 DB에서 원자적으로 병합한다.

    `video.merge_extra`의 클립 버전이다.
    DB가 병합하므로 행 잠금 없이도 다른 워커가 쓴 키를 지우지 않는다.
    `clip.extra = {…}`로 치환하면 썸네일 워커가 ffmpeg를 실행하는 수십 초 사이에
    다른 워커가 쓴 키가 지워진다. `process_one_by_id`처럼 행 잠금이 없는 경로도 있다.

    Args:
        patch: 덮어쓸 key/value. 준 key만 새 값으로 바뀌고 나머지는 남는다.
        remove: 지울 key 목록. 성공했을 때 실패 기록(error·retry)을 지우는 데 쓴다.

    Note:
        이 호출 뒤 ORM 인스턴스의 `extra`는 낡은 값이다. 다시 읽어야 한다.
    """
    expr = Clip.extra.op("||")(cast(patch, JSONB))
    for key in remove:
        expr = expr.op("-")(key)
    await db.execute(sa_update(Clip).where(Clip.clip_id == clip_id).values(extra=expr))
