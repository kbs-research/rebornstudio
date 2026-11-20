"""영상 원본 멀티파트 업로드의 시작·파트 서명·확정."""
import logging
from functools import partial
from uuid import UUID

import anyio
from fastapi import HTTPException, status
from reborn_contracts import (
    VideoDTO,
    VideoMultipartInitiateResponse,
    VideoPartUrlsRequest,
    VideoPartUrlsResponse,
    VideoUploadCompleteRequest,
    VideoUploadLimits,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.models import Project, User, Video
from api_gateway.services import audit, platform_credentials
from api_gateway.services.media import stage_runs, storage
from api_gateway.services.media.video.access import _load_for_admin, _require_member
from api_gateway.services.media.video.commands import _VIDEO_EXTS, merge_extra, reject_unuploadable
from api_gateway.services.media.video.dto import _to_dto
from api_gateway.services.media.video.keys import _upload_key
from api_gateway.services.transient import is_transient_boto
from api_gateway.services.work import service as work_service

log = logging.getLogger(__name__)


async def _off_loop(fn, *args, **kwargs):
    """동기 저장소 호출(boto3)을 스레드에서 돌리고 결과를 돌려준다.

    API는 uvicorn 프로세스 하나라서 저장소 왕복을 루프에서 기다리면 그동안 다른 요청이 모두 멈춘다.
    함수는 부를 때마다 `storage` 모듈 이름으로 찾으므로 테스트가 바꿔 끼운 함수도 그대로 불린다.
    """
    return await anyio.to_thread.run_sync(partial(fn, *args, **kwargs))


async def upload_limits(db: AsyncSession, project_id: UUID, user: User) -> VideoUploadLimits:
    """로컬 업로드의 용량 상한과 허용 확장자를 돌려준다. 등록 시트가 열릴 때 한 번 조회한다.

    화면은 이 값으로 큰 파일을 영상 레코드를 만들기 전에 거르고,
    확장자 목록의 사본을 따로 두지 않는다.
    모든 프로젝트가 같은 값이지만 값이 프로젝트별로 달라질 수 있도록 경로를 프로젝트 아래에 둔다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 `project_not_found`),
            멤버가 아닌 경우(403 `not_a_member`).
    """
    if not await db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await _require_member(db, project_id, user)
    return VideoUploadLimits(
        max_bytes=settings.s3_max_upload_bytes,
        video_extensions=sorted(_VIDEO_EXTS),
    )


# 멀티파트 파트 크기. S3는 파트 하나를 5 MiB 이상, 파트 수를 10,000개 이하로 제한한다.
# 파트 수는 업로드 상한 ÷ 16 MiB다. 상한 5 GiB(`s3_max_upload_bytes`)는 320개라 10,000개 안에 든다.
# 주의: 중복 감지의 내용 해시가 파트 크기에 따라 달라진다.
# 파트 크기를 바꾸면 같은 파일도 해시가 달라져 기존 해시와 대조할 수 없다.
_PART_SIZE = 16 * 1024 * 1024


async def initiate_multipart(
    db: AsyncSession, video_id: UUID, user: User
) -> VideoMultipartInitiateResponse:
    """멀티파트 업로드를 시작한다. 진행 중이던 업로드가 있으면 이어받는다.

    같은 영상에 다시 요청하면 새로 시작하지 않고 기존 upload_id와 올라간 파트 번호를
    돌려준다. 그래서 브라우저가 탭을 닫았다가 돌아와도 이어서 올릴 수 있다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            이미 업로드된 경우(409 `already_uploaded`),
            등록한 크기·길이로 보아 쓸 수 없는 파일인 경우(422, `reject_unuploadable`).
    """
    video = await _load_for_admin(db, video_id, user)
    # 영상에 기록한 저장소 연결이 이 프로세스에 아직 적재되지 않았으면(방금 고른 연결) 다시 읽는다.
    await platform_credentials.ensure_storage_loaded(db, storage.pin_of(video.extra))
    if video.upload_status == "uploaded":
        raise HTTPException(status.HTTP_409_CONFLICT, "already_uploaded")
    reject_unuploadable(video.file_size_bytes, video.duration_seconds)

    key = _upload_key(video)
    extra = {**(video.extra or {})}
    store = storage.for_video(extra)
    upload_id = extra.get("mpu_upload_id")
    uploaded: list[int] = []
    if upload_id:
        try:
            uploaded = sorted(
                p["PartNumber"]
                for p in await _off_loop(storage.list_parts, key, upload_id, store=store))
        except Exception:  # noqa: BLE001 — 만료·중단된 id면 새로 시작한다
            upload_id = None
    if not upload_id:
        upload_id = await _off_loop(storage.create_multipart, key, store=store)
        # 위 `extra`는 S3 왕복 전에 읽은 값이라 되쓰면 그 사이 다른 워커가 쓴 키가 지워진다.
        # 그래서 키만 병합한다.
        await merge_extra(db, video.video_id, {"mpu_upload_id": upload_id})
        # 업로드 시작을 기록하고 `complete_upload`가 닫는다. 이어받기는 시도 기록을 더하지 않는다.
        await stage_runs.start(db, video.video_id, "upload", detail={"source": "file"})
    await db.commit()
    return VideoMultipartInitiateResponse(
        upload_id=upload_id, s3_key=key, part_size=_PART_SIZE,
        max_bytes=settings.s3_max_upload_bytes, uploaded_parts=uploaded,
    )


async def sign_upload_parts(
    db: AsyncSession, video_id: UUID, payload: VideoPartUrlsRequest, user: User
) -> VideoPartUrlsResponse:
    """요청한 번호의 파트 URL만 서명한다.

    전체 파트를 한 번에 서명하면 뒤쪽 파트를 올릴 때쯤 URL이 만료된다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            이미 업로드된 경우(409 `already_uploaded`).
    """
    video = await _load_for_admin(db, video_id, user)
    # 영상에 기록한 저장소 연결이 이 프로세스에 아직 적재되지 않았으면(방금 고른 연결) 다시 읽는다.
    await platform_credentials.ensure_storage_loaded(db, storage.pin_of(video.extra))
    if video.upload_status == "uploaded":
        raise HTTPException(status.HTTP_409_CONFLICT, "already_uploaded")
    urls = storage.presign_parts(_upload_key(video), payload.upload_id, payload.part_numbers,
                                 store=storage.for_video(video.extra))
    return VideoPartUrlsResponse(urls=urls, expires_in=settings.s3_presign_ttl_seconds)


def _discard_rejected(key: str, store: storage.Store) -> None:
    """완료 확인에서 거절한 원본 객체를 지운다. 삭제가 실패해도 거절 응답은 바꾸지 않는다.

    멀티파트 업로드는 파트를 합친 뒤에야 크기를 알 수 있어 상한을 넘는 객체가 버킷에 남는다.
    영상 행은 그 객체를 쓰지 않으므로 남겨 두면 저장 비용만 든다.
    삭제 실패를 응답으로 올리면 크기 초과라는 거절 사유가 가려지므로 로그로만 남긴다.
    일시 오류(스로틀·5xx·순단)는 경고로, 권한 같은 영구 오류는 오류로 남긴다.
    지우지 못한 객체는 같은 영상으로 다시 올릴 때 같은 키에 덮어쓴다.
    """
    try:
        storage.delete_object(bucket=store.bucket, key=key, store=store)
    except Exception as e:  # noqa: BLE001
        if is_transient_boto(e):
            log.warning("거절한 업로드 객체 삭제 실패(일시 오류) key=%s: %s", key, type(e).__name__)
        else:
            log.error("거절한 업로드 객체 삭제 실패 key=%s: %s", key, type(e).__name__)


async def complete_upload(
    db: AsyncSession, video_id: UUID, payload: VideoUploadCompleteRequest, user: User
) -> VideoDTO:
    """S3 HEAD로 업로드를 확인하고 `source_uri`와 `upload_status`를 확정한다.

    cleared 영상이면 work_item을 자동으로 만든다.

    Raises:
        HTTPException: 영상이 없는 경우(404 `video_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`),
            이미 업로드된 경우(409 `already_uploaded`),
            객체가 올라오지 않은 경우(400 `object_not_uploaded`),
            크기가 기대값과 다른 경우(400 `size_mismatch`),
            올라온 파일이 비었거나 상한을 넘는 경우(422 `video_file_empty`·`video_file_too_large`).
            이때 올라온 객체를 지운다(`_discard_rejected`).
    """
    video = await _load_for_admin(db, video_id, user)
    # 영상에 기록한 저장소 연결이 이 프로세스에 아직 적재되지 않았으면(방금 고른 연결) 다시 읽는다.
    await platform_credentials.ensure_storage_loaded(db, storage.pin_of(video.extra))
    if video.upload_status == "uploaded":
        raise HTTPException(status.HTTP_409_CONFLICT, "already_uploaded")

    key = _upload_key(video)
    store = storage.for_video(video.extra)
    # 멀티파트 업로드면 여기서 파트를 하나로 합친다.
    # 파트 목록은 서버가 ListParts로 만들므로 클라이언트는 ETag를 보관하지 않아도 된다.
    mpu = payload.upload_id or (video.extra or {}).get("mpu_upload_id")
    if mpu and await _off_loop(storage.head, key, store=store) is None:
        parts = await _off_loop(storage.list_parts, key, mpu, store=store)
        if not parts:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "object_not_uploaded")
        await _off_loop(storage.complete_multipart, key, mpu, parts, store=store)
    meta = await _off_loop(storage.head, key, store=store)
    if meta is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "object_not_uploaded")

    # S3 메타의 크기를 기준으로 한다. payload의 크기는 대조에만 쓰고 다르면 400을 낸다.
    actual = meta.size_bytes or 0
    if payload.expected_size_bytes is not None and payload.expected_size_bytes != actual:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "size_mismatch")
    # 멀티파트에는 업로드 정책(크기 범위)이 없어 상한을 넘는 파일도 합쳐진다.
    # 그래서 실제 크기로 다시 검사하고, 거절한 객체는 지운다.
    try:
        reject_unuploadable(actual, None)
    except HTTPException:
        _discard_rejected(key, store)
        raise

    video.source_uri = storage.s3_uri(key, store=store)
    video.upload_status = "uploaded"
    video.file_size_bytes = actual

    # 업로드 키가 video_id별로 나뉘어 같은 파일을 다시 올려도 키가 충돌하지 않는다.
    # 그래서 같은 프로젝트 안에서 내용 지문을 대조해 중복을 표시한다.
    # 같은 원본을 정책을 바꿔 다시 올리는 정당한 경우가 있어 차단하지 않고 사람이 판단하게 한다.
    etag = meta.etag or ""
    # `video.extra`는 `storage.head` 왕복 전에 적재된 값이라 더할 값만 모아 DB에서 병합한다.
    # 이어받기 상태(`mpu_upload_id`)는 `remove=`로 지운다.
    patch: dict = {}
    # 한 번에 올린 객체(ETag에 `-`가 없음)는 ETag가 내용 MD5라 그대로 쓴다.
    # 멀티파트 ETag는 파트 해시의 해시라서 클라이언트가 보낸 내용 해시를 쓴다.
    # 파트 크기가 고정이라 같은 파일은 같은 해시가 나온다.
    fingerprint = etag if (etag and "-" not in etag) else (payload.content_hash or "")
    if fingerprint:
        dup = (await db.execute(
            select(Video.video_id, Video.title).where(
                Video.project_id == video.project_id,
                Video.video_id != video.video_id,
                Video.deleted_at.is_(None),
                Video.extra["content_etag"].astext == fingerprint,
            ).limit(1)
        )).first()
        patch["content_etag"] = fingerprint
        if dup:
            patch["duplicate_of"] = str(dup.video_id)
            patch["duplicate_title"] = dup.title
            audit.emit(
                db, event_type="video.duplicate_detected", target_kind="video",
                target_id=video.video_id, actor_user_id=user.user_id,
                project_id=video.project_id,
                payload={"duplicate_of": str(dup.video_id), "etag": fingerprint},
            )
    await merge_extra(db, video.video_id, patch, remove=("mpu_upload_id",))
    # 화면이 로컬 업로드의 크기와 위치를 표시하도록 출처와 함께 위치·크기를 이력에 남긴다.
    await stage_runs.finish(db, video.video_id, "upload", ok=True, detail={
        "source": "file", "bucket": store.bucket, "key": key, "bytes": actual,
    })
    # cleared 영상이면 work_item을 자동으로 만든다.
    await work_service.ensure_for_video(db, video)
    audit.emit(
        db, event_type="video.uploaded", target_kind="video", target_id=video.video_id,
        actor_user_id=user.user_id, project_id=video.project_id,
        payload={"bytes": actual},
    )
    await db.commit()
    await db.refresh(video)
    return _to_dto(video)
