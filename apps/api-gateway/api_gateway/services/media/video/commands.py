"""영상 등록·수정·삭제와 메타데이터 병합."""
from datetime import UTC, datetime
from functools import partial
from uuid import UUID

import anyio
from fastapi import HTTPException, status
from reborn_contracts import VideoCreateRequest, VideoDTO, VideoUpdateRequest
from reborn_contracts.video import ORIGINAL_FILENAME_KEY, clean_original_filename
from sqlalchemy import cast, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import Project, User, Video, WorkItem
from api_gateway.models.video import INITIAL_COPYRIGHT_STATUS
from api_gateway.services import audit, authz, platform_credentials
from api_gateway.services.media import app_paths, ingest_batch, stage_runs, storage
from api_gateway.services.media.video.dto import _to_dto
from api_gateway.services.media.video.keys import _upload_key
from api_gateway.services.stacks import stack_lock
from api_gateway.services.work import service as work_service

# 스토리지 일괄 등록에서 허용하는 영상 확장자. 폴더에 섞인 json·메타 파일의 오등록을 막는다.
_VIDEO_EXTS = {"mp4", "mov", "mkv", "avi", "ts", "m4v", "webm", "mxf"}


def reject_unuploadable(file_size_bytes: int | None, duration_seconds: float | None) -> None:
    """올려도 쓸 수 없는 파일이면 422를 던진다. 영상 행을 만들기 전과 업로드를 시작하기 전에 부른다.

    0바이트 파일은 업로드가 완료되지 않는다.
    용량·길이 상한을 넘는 파일은 업로드 정책과 규격 검증이 거절한다.
    세 경우 모두 행을 먼저 만들면 끝낼 수 없는 업로드가 "업로드 중"으로 남는다.
    값이 없으면(`None`) 상한을 넘었는지 알 수 없으므로 통과시킨다.

    Raises:
        HTTPException: 0바이트인 경우(422 `video_file_empty`),
            용량 상한을 넘는 경우(422 `video_file_too_large`),
            길이 상한을 넘는 경우(422 `video_too_long`).
    """
    if file_size_bytes is not None and file_size_bytes <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "video_file_empty")
    if file_size_bytes is not None and file_size_bytes > settings.s3_max_upload_bytes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "video_file_too_large")
    if duration_seconds is not None and duration_seconds > settings.probe_max_duration_seconds:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "video_too_long")


def _bucket_filename(name: str) -> str | None:
    """버킷 키의 마지막 부분을 원래 파일 이름으로 돌려준다. 제어 문자가 있으면 `None`이다.

    버킷 키는 사람이 입력한 값이 아니므로 제어 문자가 있어도 등록은 하고 이름만 남기지 않는다.
    """
    try:
        return clean_original_filename(name)
    except ValueError:
        return None


async def create(
    db: AsyncSession, project_id: UUID, payload: VideoCreateRequest, user: User
) -> VideoDTO:
    """영상 메타데이터를 등록하고 VideoDTO를 돌려준다.

    권리 상태(`copyright_status`)는 `INITIAL_COPYRIGHT_STATUS`(`cleared`)로 저장한다.
    정책 값(`copyright_policy`)은 요청 값(`pre_cleared`)을 그대로 저장한다.

    올려도 쓸 수 없는 파일(0바이트·상한 초과)은 행을 만들기 전에 거절한다(`reject_unuploadable`).

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 `project_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            쓸 수 없는 파일인 경우(422 `video_file_empty`·`video_file_too_large`·`video_too_long`).
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)
    reject_unuploadable(payload.file_size_bytes, payload.duration_seconds)
    # 스택 저장과 동시에 실행돼도 영상과 프로젝트가 같은 스택 선택을 가리키도록
    # 설정을 읽기 전에 프로젝트 행을 공유 잠금한다(`stack_lock`).
    project = await stack_lock.hold_for_new_video(db, project_id) or project

    extra: dict = {}
    # 납품 패키지의 `sources.csv`가 원본 파일을 가리키는 참고 정보로 원래 파일 이름을 쓴다.
    # 업로드 키와 제목에는 파일 이름이 남지 않는다. 계약 모델이 경로를 떼고 이름만 넘긴다.
    if payload.original_filename:
        extra[ORIGINAL_FILENAME_KEY] = payload.original_filename
    # 프로젝트가 고른 저장소를 영상 행에 고정 기록한다. 업로드 서명과 산출 저장이 이 값을 쓴다.
    await platform_credentials.ensure_storage_loaded(
        db, platform_credentials.storage_target_of(project.settings))
    pin = storage.pin_for(project.settings)
    if pin is not None:
        extra[storage.PIN_KEY] = pin
    video = Video(
        project_id=project_id,
        uploaded_by=user.user_id,
        title=payload.title,
        copyright_policy=payload.copyright_policy,
        copyright_status=INITIAL_COPYRIGHT_STATUS,
        transcode_profile=payload.transcode_profile,
        duration_seconds=payload.duration_seconds,
        file_size_bytes=payload.file_size_bytes,
        broadcaster=payload.broadcaster,
        program_name=payload.program_name,
        genre=payload.genre,
        episode=payload.episode,
        aired_at=payload.aired_at,
        upload_batch_id=payload.upload_batch_id,
        extra=extra,
    )
    # videos.upload_batch_id에 FK가 있어 묶음 행을 먼저 만든다. 순서를 바꾸면 FK 위반이 난다.
    # 화면이 묶음 id를 보내지 않았으면 서버가 만든 id를 붙인다.
    video.upload_batch_id = await ingest_batch.ensure_batch(
        db, project_id=project_id, batch_id=payload.upload_batch_id, user=user, source="upload",
    )
    db.add(video)
    await db.commit()
    await db.refresh(video)
    return _to_dto(video)


async def register_from_storage(
    db: AsyncSession, project_id: UUID, keys: list[str],
    copyright_policy: str, user: User, upload_batch_id: UUID | None = None,
    *, broadcaster: str | None = None, program_name: str | None = None,
    transcode_profile: str = "auto",
    bucket: str | None = None,
) -> dict:
    """NCP 버킷 키 목록을 영상으로 일괄 등록한다. 폴더 요청 흐름의 등록 단계다.

    전체 흐름은 등록, 분석 요청, MAIU 분석(scene 클립 생성), 라벨링 순서다.
    권리 상태(`copyright_status`)는 업로드 경로(`create`)와 같은
    `INITIAL_COPYRIGHT_STATUS`(`cleared`)로 저장한다.
    쓸 수 없는 영상의 분석비는 등록 단계가 아니라 분석 요청 게이트가 막는다.

    업로드 확정 후처리와 같게 upload_status='uploaded'로 등록한다.
    같은 `source_uri`가 이미 있어도 등록하고 `extra.duplicate_of`에 기존 영상을 기록한다.

    Returns:
        dict: `register_keys`의 결과(registered·skipped·duplicates·invalid·
            work_items_created·video_ids)에 `batch_id`를 더한 값.
            중복을 막지 않으므로 `skipped`는 늘 0이다.
            영상이 아닌 확장자는 `invalid`로 센다.
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)

    # NCP 등록도 업로드와 같은 묶음 기준을 쓰고 출처(`source`)로 유입 경로를 구분한다.
    # Job이 없는 영상은 Job 보드에 표시되지 않아서 묶음 id를 보내지 않으면 서버가 만든다.
    upload_batch_id = await ingest_batch.ensure_batch(
        db, project_id=project_id, batch_id=upload_batch_id, user=user, source="ncp",
    )
    result = await register_keys(
        db, project_id, keys, copyright_policy,
        created_by=user.user_id, upload_batch_id=upload_batch_id,
        broadcaster=broadcaster, program_name=program_name,
        transcode_profile=transcode_profile,
        bucket=bucket,
    )
    await db.commit()
    # 서버가 만든 묶음 id는 이 응답으로만 알 수 있으므로 함께 돌려준다.
    return {**result, "batch_id": upload_batch_id}


def first_missing_key(keys: list[str], *, bucket: str, store: storage.Store) -> str | None:
    """등록할 영상 키 중 저장소에 확실히 없는 첫 키를 돌려준다. 모두 있으면 `None`이다.

    키를 직접 입력해 등록하는 경로가 등록 전에 부른다. 오타로 없는 원본을 등록하면 규격 검증이
    실패할 때까지 문제 영상으로 남는다. 등록하지 않을 키(영상 확장자가 아니거나 앱 경로 아래)는
    `register_keys`가 `invalid`로 세므로 확인하지 않는다.
    저장소가 확인하지 못한 키(권한·순단, `object_exists`가 None)는
    규격 검증이 다시 확인하므로 막지 않는다.
    저장소를 키마다 호출하므로 스레드에서 부른다.
    """
    for key in keys:
        if key.rsplit(".", 1)[-1].lower() not in _VIDEO_EXTS or app_paths.is_app_key(key):
            continue
        if storage.object_exists(bucket=bucket, key=key, store=store) is False:
            return key
    return None


async def register_keys(
    db: AsyncSession, project_id: UUID, keys: list[str], copyright_policy: str,
    *, created_by: UUID, upload_batch_id: UUID | None,
    broadcaster: str | None = None, program_name: str | None = None,
    transcode_profile: str = "auto",
    bucket: str | None = None,
    skip_existing: bool = False,
) -> dict:
    """키 목록으로 Video 행을 등록한다. 권한 확인과 커밋을 하지 않는 내부 공통 함수다.

    두 호출자가 함께 쓰고, 이 함수는 HTTPException을 내지 않는다.
    - API `register_from_storage`: 사람이 키를 골라 요청한 경로이고 권한을 확인한 뒤 부른다.
      고른 파일이 이미 등록돼 있어도 다시 등록하고 `duplicate_of`로 표시만 한다.
    - worker의 폴더 단위 등록 잡(`storage_import`): 권한은 큐에 넣을 때 확인했다.
      사용자 객체가 없어 `created_by` UUID만 받고 `skip_existing`을 참으로 넘긴다.

    같은 폴더를 다시 등록하는 목적은 그사이 추가된 파일을 넣는 일이라
    폴더 단위 등록은 이미 등록한 원본을 건너뛴다.
    기존 파일까지 다시 등록하면 영상·작업·분석 비용이 두 배가 된다.
    폴더 단위 등록 잡은 등록과 이어받기 토큰 전진을 한 커밋으로 묶는다.
    그래서 실패한 페이지는 통째로 롤백되고 다시 순회된다(`storage_import._run_one`).

    Args:
        skip_existing: 참이면 같은 프로젝트에 삭제되지 않은 같은 원본 URI가 있는 키를 등록하지 않고
            `skipped`로 센다.

    Returns:
        dict: registered, skipped, duplicates, invalid, work_items_created, video_ids.
            영상 확장자가 아니거나 앱 경로 아래에 있는 키는 `invalid`로 센다.
            `skip_existing`으로 건너뛴 키는 `skipped`로 센다.
    """
    # 영상 확장자만 등록해 폴더에 섞인 json·메타 파일의 오등록을 막는다.
    # 폴더 단위 등록 작업은 큐에 넣을 때 prefix만 검사하므로, 목록에 섞인 앱 경로 아래 키
    # (다른 프로젝트의 산출물)를 여기서 키마다 다시 거른다(`app_paths.is_app_key`).
    video_keys = [k for k in keys if k.rsplit(".", 1)[-1].lower() in _VIDEO_EXTS
                  and not app_paths.is_app_key(k)]
    invalid = len(keys) - len(video_keys)

    # 탐색과 등록이 같은 버킷을 가리키도록 화면이 고른 버킷으로 URI를 만든다.
    # 화면이 버킷을 고르지 않았으면 프로젝트가 고른 버킷을 쓴다.
    # 설정을 읽기 전에 프로젝트 행을 공유 잠금한다(`stack_lock`, `create`와 같은 이유).
    held = await stack_lock.hold_for_new_video(db, project_id)
    project_settings = held.settings if held is not None else None
    await platform_credentials.ensure_storage_loaded(
        db, platform_credentials.storage_target_of(project_settings))
    bucket = bucket or storage.for_project(project_settings).bucket
    # 산출은 원본 버킷이 아니라 프로젝트 저장소에 둔다. 그 저장소를 영상마다 고정 기록한다.
    pin = storage.pin_for(project_settings)
    uris = {key: f"s3://{bucket}/{key}" for key in video_keys}
    # 같은 원본을 정책·길이를 바꿔 다시 돌리는 것은 정당한 사용이라서
    # 사람이 고른 키는 같은 오브젝트도 다시 등록하고 `duplicate_of`로 표시만 한다.
    # 파일 업로드 경로(`complete_upload`)도 같다.
    # 폴더 단위 등록(`skip_existing`)은 파일을 하나씩 고르지 않으므로 이미 있는 원본을 건너뛴다.
    dup_of = dict((await db.execute(
        select(Video.source_uri, Video.video_id).where(
            Video.project_id == project_id,
            Video.source_uri.in_(uris.values()),
            Video.deleted_at.is_(None),
        )
    )).all())
    if skip_existing:
        uris = {key: uri for key, uri in uris.items() if uri not in dup_of}

    registered: list[Video] = []
    work_items = 0
    duplicates = 0
    for key, uri in uris.items():
        name = key.rsplit("/", 1)[-1]
        title = name.rsplit(".", 1)[0] or name
        # 버킷 키의 마지막 부분이 원래 파일 이름이다. 업로드 경로와 같은 키에 둔다.
        extra: dict = {"registered_from": "storage_browse", "key": key}
        original = _bucket_filename(name)
        if original:
            extra[ORIGINAL_FILENAME_KEY] = original
        if pin is not None:
            extra[storage.PIN_KEY] = pin
        # 같은 오브젝트가 이미 있으면 화면이 "다시 등록됨"을 표시하도록 기존 영상 id를 기록한다.
        if uri in dup_of:
            extra["duplicate_of"] = str(dup_of[uri])
            duplicates += 1
        video = Video(
            project_id=project_id,
            uploaded_by=created_by,
            title=title,
            copyright_policy=copyright_policy,
            copyright_status=INITIAL_COPYRIGHT_STATUS,
            source_uri=uri,
            upload_batch_id=upload_batch_id,
            upload_status="uploaded",
            broadcaster=broadcaster,
            program_name=program_name,
            transcode_profile=transcode_profile,
            extra=extra,
        )
        db.add(video)
        await db.flush()
        # 파일 전송은 없지만 버킷 키에 연결된 시각이 준비 여정의 시작점이므로
        # `upload` 단계를 즉시 열고 닫아 소요 0인 기록으로 남긴다.
        # 버킷과 키는 `upload` 단계의 세부 항목으로 넣는다.
        await stage_runs.start(db, video.video_id, "upload", detail={
            "source": "bucket",
            "bucket": bucket,
            "key": key,
        })
        await stage_runs.finish(db, video.video_id, "upload", ok=True)
        # 영상 단위 감사 기록(`target_id=video_id`)에 유입 경로가 남도록
        # 영상마다 등록 기록을 남긴다.
        # 아래 프로젝트 요약에는 이번 등록 건수만 남는다.
        audit.emit(
            db, event_type="video.registered", target_kind="video", target_id=video.video_id,
            actor_user_id=created_by, project_id=project_id,
            payload={"bucket": bucket, "key": key, "batch_id": str(upload_batch_id)
                     if upload_batch_id else None,
                     "duplicate_of": extra.get("duplicate_of")},
        )
        if await work_service.ensure_for_video(db, video) is not None:
            work_items += 1
        registered.append(video)

    audit.emit(
        db, event_type="video.bulk_registered", target_kind="project", target_id=project_id,
        actor_user_id=created_by, project_id=project_id,
        payload={"count": len(registered), "skipped": len(video_keys) - len(registered),
                 "invalid": invalid, "duplicates": duplicates},
    )
    return {
        "registered": len(registered),
        "skipped": len(video_keys) - len(registered),
        # 다시 등록된 수(같은 오브젝트가 이미 있던 영상). 사용자가 중복 등록이 의도한 것인지
        # 판단하도록 화면이 표시한다.
        "duplicates": duplicates,
        "invalid": invalid,
        "work_items_created": work_items,
        "video_ids": [v.video_id for v in registered],
    }


async def update(
    db: AsyncSession, video_id: UUID, payload: VideoUpdateRequest, user: User
) -> VideoDTO:
    """영상 메타를 편집한다.

    대상은 제목·회차와 영상 단위 메타 네 가지(방송사·프로그램명·장르·방영일)다.
    요청에 없는 필드는 그대로 두고, 보낸 필드는 그 값으로 바꾼다. `null`을 보내면 값을 지운다.
    보내지 않은 필드와 `null`을 같게 처리하면 사용자가 비운 입력란이 성공 응답과 함께 무시된다.
    제목은 지울 수 없다(요청 모델이 422로 막는다).
    copyright_*와 upload_status는 각 도메인 라우터가 관리하므로 바꾸지 않는다.

    영상 메타 입력은 반입·준비와 같은 운영 업무라 관리자가 편집한다.
    네 가지 메타는 그 영상의 모든 클립이 물려받으므로(납품 메타·품질 검수)
    바뀐 필드를 감사 기록에 남긴다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`).
    """
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_project_admin(db, video.project_id, user)
    changed = apply_meta(video, payload)
    if changed:
        audit.emit(
            db, event_type="video.updated", target_kind="video",
            target_id=video.video_id, actor_user_id=user.user_id, project_id=video.project_id,
            payload={"changed": changed},
        )
    await db.commit()
    await db.refresh(video)
    return _to_dto(video)


#: 메타 편집이 바꾸는 영상 필드. `VideoUpdateRequest`의 필드와 같다.
META_KEYS = ("title", "broadcaster", "program_name", "genre", "episode", "aired_at")


def apply_meta(video: Video, payload: VideoUpdateRequest) -> dict[str, dict[str, object]]:
    """요청이 보낸 메타 필드를 영상 행에 쓰고 바뀐 필드를 돌려준다.

    커밋과 감사 기록은 호출자가 한다.
    단건 편집(`update`)과 등록 묶음 일괄 입력(`media.meta_import.apply`)이 같이 쓴다.
    앞뒤 공백을 지우고, 공백뿐인 값은 비운 값으로 보고, 제목은 비우지 않는다.

    Returns:
        dict: 필드 키마다 `{"from": 이전 값, "to": 새 값}`. 시각은 ISO 문자열이다.
    """
    def _plain(x: object) -> object:
        """감사 payload(JSONB)에 넣을 형태로 바꾼다. 시각은 ISO 문자열로 바꾼다."""
        return x.isoformat() if isinstance(x, datetime) else x

    changed: dict[str, dict[str, object]] = {}
    sent = payload.model_fields_set
    for key in META_KEYS:
        if key not in sent:
            continue
        val = getattr(payload, key)
        if isinstance(val, str):
            val = val.strip() or None   # 공백뿐인 값은 비운 것으로 본다
        if val is None and key == "title":
            continue   # 공백뿐인 제목은 반영하지 않는다. 제목이 없으면 영상을 식별할 수 없다
        before = getattr(video, key)
        if before != val:
            changed[key] = {"from": _plain(before), "to": _plain(val)}
        setattr(video, key, val)
    return changed


async def merge_extra(
    db: AsyncSession, video_id: UUID, patch: dict, *, remove: tuple[str, ...] = ()
) -> None:
    """`videos.metadata`(JSONB)를 DB에서 원자적으로 병합한다.

    DB가 `metadata || patch`와 `- key`로 병합하므로 읽기와 쓰기 사이에 다른 쓰기가 끼어들지 않는다.
    `video.extra`에 합친 dict를 통째로 대입하면 lost update가 생긴다.
    배치 루프 안의 외부 호출(ffmpeg 프레임 추출·ffprobe·MAIU)이 수 초에서 수십 초 걸리는 동안
    다른 워커가 같은 행에 쓴 변경이 사라진다.
    예를 들어 `probe` 메타가 사라지면 `needs_transcode`가 변환 필요 여부를 판정할 수 없다.

    이 호출 뒤 ORM 인스턴스의 `extra`는 갱신 전 값이다. 필요하면 호출자가 refresh한다.
    """
    expr = Video.extra.op("||")(cast(patch, JSONB))
    for key in remove:
        expr = expr.op("-")(key)
    await db.execute(sa_update(Video).where(Video.video_id == video_id).values(extra=expr))


async def absorb_media_meta(
    db: AsyncSession,
    video: Video,
    *,
    duration_ms: float | None = None,
    size_bytes: int | None = None,
    extra_patch: dict | None = None,
) -> None:
    """외부 분석기(MAIU 등)가 보고한 미디어 메타를 반영한다.

    duration·size 열은 이 도메인이 소유하므로 다른 도메인은 직접 대입하지 않고 이 함수를 거친다.
    규칙은 probe와 같다. 빈 열만 채우고 이미 있는 값은 덮어쓰지 않는다.
    `extra_patch`는 key 단위로 병합한다.
    넘긴 key는 새 값으로 덮어쓰고(reconcile 반복 실행에 맞춘 동작) 다른 key는 보존한다.

    호출하는 reconciler는 영상을 루프 전에 적재하고 반복마다 MAIU에 HTTP 요청을 보낸다.
    적재한 값을 파이썬에서 합쳐 되쓰면 그동안 다른 워커가 쓴 key가 사라지므로 병합은 DB가 한다.

    이 호출 뒤 `video.extra`는 갱신 전 값이다. 필요하면 호출자가 refresh한다.
    """
    if duration_ms and not video.duration_seconds:
        video.duration_seconds = float(duration_ms) / 1000.0
    if size_bytes and not video.file_size_bytes:
        video.file_size_bytes = int(size_bytes)
    if extra_patch:
        await merge_extra(db, video.video_id, extra_patch)


async def soft_delete(db: AsyncSession, video_id: UUID, user: User) -> None:
    """영상을 soft delete한다. project_admin만 할 수 있다.

    업로드가 끝나지 않은 영상이면 완료되지 않은 멀티파트 업로드도 중단한다.
    중간까지 올라간 파트는 완료 전까지 과금되고 콘솔에도 보이지 않는다.
    업로드가 끝난 영상의 객체는 지우지 않는다.

    미완 작업(`_CLOSABLE`)은 닫고 승인된 작업은 납품에 남기므로 그대로 둔다.
    미완 작업을 닫지 않으면 가져간 사람의 `내 작업`에 남아 동시 작업 한도를 차지하고
    진척률 분모와 설정 화면의 작업 수에도 계속 잡힌다.
    닫는 규칙은 비식별화 신고 제외와 같은 함수(`work.service.close_items`)를 써서
    점유 해제와 감사 기록을 한 곳에서 처리한다.

    삭제는 되돌릴 수 없으므로 작업이 사라진 근거로
    감사 payload에 닫은 수와 남긴 승인 수를 함께 적는다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`).
    """
    video = await db.get(Video, video_id)
    if not video or video.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_project_admin(db, video.project_id, user)
    mpu = (video.extra or {}).get("mpu_upload_id")
    if video.upload_status != "uploaded" and mpu:
        # 멀티파트 중단이 실패해도 삭제는 계속한다. `abort_multipart`가 예외를 잡아 로그만 남긴다.
        # 영상의 저장소를 읽지 못하면 중단 요청을 보내지 않고,
        # 남은 파트는 라이프사이클 규칙이 정리한다.
        store = storage.for_video_or_none(video.extra)
        if store is not None:
            storage.abort_multipart(_upload_key(video), mpu, store=store)
        await merge_extra(db, video.video_id, {}, remove=("mpu_upload_id",))
    video.deleted_at = datetime.now(UTC)
    kept = int(await db.scalar(
        select(func.count()).select_from(WorkItem).where(
            WorkItem.video_id == video.video_id, WorkItem.status == "approved",
        )
    ) or 0)
    # 사람 작업과 디스패치 티켓(분석이 필요하다는 표식)은 둘 다 닫지만 따로 센다.
    # 한 수로 합치면 삭제 확인 창의 `작업 N건`(사람 작업만 센다)과 이 기록의 수가 달라진다.
    closed = await work_service.close_items(
        db, WorkItem.video_id == video.video_id, WorkItem.clip_id.is_not(None),
        reason="video_deleted", actor_user_id=user.user_id, project_id=video.project_id,
    )
    tickets = await work_service.close_items(
        db, WorkItem.video_id == video.video_id, WorkItem.clip_id.is_(None),
        reason="video_deleted", actor_user_id=user.user_id, project_id=video.project_id,
    )
    audit.emit(
        db, event_type="video.deleted", target_kind="video",
        target_id=video.video_id, actor_user_id=user.user_id,
        project_id=video.project_id,
        payload={
            "closed_work_items": closed,
            "closed_dispatch_tickets": tickets,
            "kept_approved_work_items": kept,
        },
    )
    await db.commit()


async def reset_probe(
    db: AsyncSession, project_id: UUID, video_id: UUID, user: User
) -> VideoDTO:
    """확정된 규격 검사(probe) 결과를 pending으로 되돌린다.

    project_admin만 할 수 있다.
    `process_pending`은 pending만 고르므로
    probe error는 한 번 기록되면 자동으로 다시 검사하지 않는다.
    인프라 문제(presign 오라우팅 등)로 error가 된 영상을 운영자가 다시 검사하게 할 때 쓴다.
    rejected도 되돌릴 수 있고, 되돌리면 다음 probe 배치(1분 주기)가 다시 검사한다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            error·rejected가 아닌 경우(409 `not_resettable`).
    """
    video = await db.get(Video, video_id)
    if not video or video.deleted_at is not None or video.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video_not_found")
    await authz.require_project_admin(db, project_id, user)
    if video.probe_status not in ("error", "rejected"):
        raise HTTPException(status.HTTP_409_CONFLICT, "not_resettable")
    video.probe_status = "pending"  # type: ignore[assignment]
    audit.emit(
        db, event_type="video.probe_reset", target_kind="video",
        target_id=video.video_id, actor_user_id=user.user_id,
        project_id=video.project_id,
    )
    await db.commit()
    await db.refresh(video)
    return _to_dto(video)


def _source_ext(probe_meta: dict | None) -> str:
    """원본 컨테이너에 맞는 확장자를 고른다.

    규격 검증이 받는 컨테이너는 mp4·mov·matroska·webm이다. mp4와 mov는 같은 계열이라 `mp4`로 둔다.
    ffprobe는 mkv와 webm을 모두 `matroska,webm`으로 보고하고, webm은 matroska의 부분 집합이라
    둘 다 `mkv`로 둔다.
    """
    fmt = {f.strip().lower() for f in str((probe_meta or {}).get("format_name") or "").split(",")}
    if fmt & {"matroska", "webm"}:
        return "mkv"
    return "mp4"


def _key_ext(key: str) -> str:
    """키 마지막 경로 조각의 확장자를 소문자로 돌려준다. 없으면 빈 문자열이다."""
    name = key.rsplit("/", 1)[-1]
    return name.rpartition(".")[2].lower() if "." in name else ""


async def ensure_input_ext(
    db: AsyncSession, video: Video, allowed: frozenset[str]
) -> tuple[str | None, tuple[str, str] | None]:
    """분석 입력 키의 확장자가 `allowed`에 있도록 원본 키 이름을 바꾸고 그 URI를 돌려준다.

    브라우저로 올린 원본은 확장자 없는 키(`intake/{id}/source`)에 있다.
    원본이 이미 분석 규격이라 변환을 건너뛰면 분석본이 없어 원본이 그대로 분석 입력이 된다.
    키 확장자로 형식을 판단하는 분석 서비스는 이 입력을 형식 오류로 거절한다.
    그래서 원본을 같은 경로의 확장자 있는 키(`intake/{id}/source.mp4`)로 서버 쪽 복사하고
    `source_uri`를 바꾼다.

    복사본은 분석본(`analysis_uri`)으로 두지 않는다. 납품은 분석본이 있으면 분석이 원본과
    다른 파일을 읽었다고 보고 시작 시각을 보정한다(`exports.export_package.offset_of`).
    바이트가 같은 사본을 분석본으로 두면 보정이 필요 없는 영상을 보정한다.

    서버가 정한 업로드 위치(`intake/`)의 키만 바꾼다. 저장소에서 등록한 파일은 고객 버킷의
    키라서 이름을 바꾸지 않는다.

    이전 키는 지우지 않고 돌려주고, 호출자가 커밋한 뒤에 지운다.
    커밋 전에 지우면 롤백됐을 때 `source_uri`가 지워진 키를 가리킨다.

    Returns:
        tuple: (분석 입력 URI, 커밋 뒤 지울 옛 원본의 (버킷, 키)). 바꾸지 않았으면 둘째는 None이다.

    Raises:
        Exception: 서버 쪽 복사가 실패한 경우. 저장소 어댑터의 예외를 그대로 낸다.
    """
    from api_gateway.services.media import resolver
    from api_gateway.services.media.video.keys import _parse_s3_uri

    uri = resolver.media_uri(video, "analysis_input")
    if not uri or video.analysis_uri or uri != video.source_uri:
        return uri, None
    bucket, key = _parse_s3_uri(uri)
    if _key_ext(key) in allowed or not key.startswith("intake/"):
        return uri, None
    dst = f"{key}.{_source_ext((video.extra or {}).get('probe'))}"
    await anyio.to_thread.run_sync(
        partial(storage.copy_object, bucket=bucket, src_key=key, dst_key=dst))
    scheme = uri.split("://", 1)[0]
    video.source_uri = f"{scheme}://{bucket}/{dst}"
    await merge_extra(db, video.video_id, {
        "source_renamed": {"from": key, "to": dst, "at": datetime.now(UTC).isoformat()},
    })
    return video.source_uri, (bucket, key)
