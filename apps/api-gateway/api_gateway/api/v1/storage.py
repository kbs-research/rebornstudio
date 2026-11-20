"""저장소 API 라우터(project_admin).

입력한 버킷 키 목록과 폴더 경로로 영상을 등록한다. 버킷 안을 나열하는 라우트는 두지 않는다.
"""
from functools import partial
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from reborn_contracts import (
    StoragePrefixImportRequest,
    StoragePrefixImportResult,
    StorageRegisterRequest,
    StorageRegisterResult,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_current_user, get_db
from api_gateway.models import Project, User
from api_gateway.services import authz, platform_credentials
from api_gateway.services.analysis import presets
from api_gateway.services.media import app_paths, storage, storage_import
from api_gateway.services.media import video as video_service
from api_gateway.services.stacks import project_stack, project_storage

#: 입력한 키가 저장소에 없을 때의 오류 코드 머리. 뒤에 `:`와 첫 번째 없는 키를 붙인다.
KEY_NOT_FOUND = "storage_key_not_found"


async def _require_storage(db: AsyncSession, project_id: UUID) -> storage.Store:
    """프로젝트가 고른 저장소 값을 돌려준다.

    저장소 호출의 예외는 아래 라우트들이 503 `storage_unavailable`로 바꾼다.
    저장소를 고르지 않았거나 연결을 읽지 못한 상태는 다시 시도해도 풀리지 않으므로
    호출 전에 409로 따로 알린다.

    프로젝트 버킷이 그 연결의 허용 범위 밖이면 저장소를 부르지 않는다
    (`project_storage.require_allowed`). 키 등록과 폴더 등록이 모두 이 함수를 거친다.

    Raises:
        HTTPException: 저장소 대상을 기록하지 않은 경우(409 storage_stack_unset),
            버킷이 연결의 허용 범위 밖인 경우(422 storage_bucket_not_allowed).
        platform_credentials.CredentialsMissingError: 고른 연결을 읽지 못한 경우.
            api가 409로 바꾼다.
    """
    project = await db.get(Project, project_id)
    project_settings = project.settings if project is not None else None
    target = platform_credentials.storage_target_of(project_settings)
    if target is None or not target.complete:
        raise HTTPException(status.HTTP_409_CONFLICT, project_storage.UNSET)
    await platform_credentials.ensure_storage_loaded(db, target)
    await project_storage.require_allowed(db, target)
    if project is not None:
        presets.require(presets.of_project(project_settings), project_settings=project_settings)
    return storage.for_project(project_settings)


def _require_project_bucket(store: storage.Store, bucket: str | None) -> str:
    """요청한 버킷이 프로젝트 저장소의 버킷인지 확인하고 쓸 버킷을 돌려준다.

    버킷을 비우면 프로젝트 버킷을 쓴다. 프로젝트 범위의 등록은 그 프로젝트 저장소의 버킷
    하나만 받는다. 다른 버킷의 원본은 프로젝트 연결의 키로 읽지 못할 수 있어 규격 검증·변환이
    등록 뒤에 실패한다. 판정은 이 함수 한 곳에서 한다.

    Raises:
        HTTPException: 다른 버킷인 경우(422 storage_bucket_not_project).
    """
    if bucket and bucket != store.bucket:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="storage_bucket_not_project",
        )
    return store.bucket


router = APIRouter()


@router.post(
    "/projects/{project_id}/storage/register",
    response_model=StorageRegisterResult,
    summary="NCP 키 일괄 영상 등록 (0609 D 2차, project_admin)",
    tags=["storage"],
)
async def register_from_storage(
    project_id: UUID,
    payload: StorageRegisterRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StorageRegisterResult:
    """버킷 키 목록을 영상으로 등록한다. project_admin만 부를 수 있다.

    키는 사람이 입력한 값이라 등록 전에 영상 키마다 저장소에 있는지 확인한다(`first_missing_key`).
    하나라도 확실히 없으면 아무것도 등록하지 않고 첫 번째 없는 키를 알린다.
    영상은 cleared 상태로 등록되어 바로 MI 요청을 할 수 있다.
    작업은 MAIU가 만든 클립 단위로 진행한다(등록, MAIU 분석, 라벨링, 검수 순서).
    영상 확장자가 아닌 키는 제외한다.
    이미 등록된 오브젝트도 다시 등록하고 `extra.duplicate_of`로 기존 영상을 가리킨다.
    앱이 만든 경로(변환본·업로드 원본 등) 아래의 키는 받지 않는다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 project_not_found).
            project_admin이 아닌 경우(403 not_project_admin).
            앱이 만든 경로의 키가 있는 경우(400 system_prefix).
            저장소 연동이 없는 경우(409 storage_not_configured).
            버킷이 연결의 허용 범위 밖인 경우(422 storage_bucket_not_allowed).
            프로젝트 저장소가 아닌 버킷인 경우(422 storage_bucket_not_project).
            분석 대상을 기록하지 않았거나 점검이 실패한 경우(409, `project_stack.gate`).
            저장소에 없는 키가 있는 경우(422 `storage_key_not_found:<키>`).
    """
    # 권한을 저장소 상태보다 먼저 본다. 거꾸로면 관리자가 아닌 멤버가 409 응답으로 저장소 설정
    # 여부를 알아내고 연결 적재를 일으킨다.
    await authz.require_project_admin(db, project_id, user)
    if any(app_paths.is_app_key(k) for k in payload.keys):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="system_prefix")
    store = await _require_storage(db, project_id)
    bucket = _require_project_bucket(store, payload.bucket)
    await project_stack.gate(db, project_id, user)
    missing = await anyio.to_thread.run_sync(
        partial(video_service.first_missing_key, payload.keys, bucket=bucket, store=store)
    )
    if missing is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{KEY_NOT_FOUND}:{missing}",
        )
    result = await video_service.register_from_storage(
        db, project_id, payload.keys, payload.copyright_policy, user,
        upload_batch_id=payload.upload_batch_id,
        broadcaster=payload.broadcaster,
        program_name=payload.program_name,
        transcode_profile=payload.transcode_profile,
        bucket=bucket,
    )
    return StorageRegisterResult(**result)


@router.post(
    "/projects/{project_id}/storage/register-prefix",
    response_model=StoragePrefixImportResult,
    summary="폴더째 영상 등록 — 서버 비동기 작업 (project_admin)",
    tags=["storage"],
)
async def register_prefix(
    project_id: UUID,
    payload: StoragePrefixImportRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StoragePrefixImportResult:
    """prefix 아래 전체를 등록하는 작업을 큐에 넣는다. 등록은 worker beat가 한다.

    project_admin만 부를 수 있다.
    버킷 순회는 브라우저 대신 서버가 한다. 묶음 행은 요청 즉시 생긴다.
    진행 상황은 그 행의 영상 수와 import_status로 확인한다.
    탭을 닫아도 진행되고, worker가 멈췄다가 다시 시작하면 이어서 한다.

    앱이 만든 경로(변환본·업로드 원본 등)와 그 아래는 받지 않는다.
    목록에 앱 경로의 키가 섞이는 prefix(`intak`·`t`·버킷 루트)도 받지 않는다
    (`app_paths.prefix_reaches_app`).

    Raises:
        HTTPException: 권한이 없는 경우(403). 프로젝트가 없는 경우(404).
            빈 폴더인 경우(400 empty_prefix). 앱이 만든 경로인 경우(400 system_prefix).
            batch_id가 중복인 경우(409). 저장소 연동이 없는 경우(409 storage_not_configured).
            버킷이 연결의 허용 범위 밖인 경우(422 storage_bucket_not_allowed).
            프로젝트 저장소가 아닌 버킷인 경우(422 storage_bucket_not_project).
            분석 대상을 기록하지 않았거나 점검이 실패한 경우(409, `project_stack.gate`).
            NCP 조회에 실패한 경우(503).
    """
    # 권한을 저장소 상태보다 먼저 본다(`register_from_storage`와 같은 이유).
    await authz.require_project_admin(db, project_id, user)
    if app_paths.prefix_reaches_app(payload.prefix):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="system_prefix")
    store = await _require_storage(db, project_id)
    bucket = _require_project_bucket(store, payload.bucket)
    await project_stack.gate(db, project_id, user)
    batch = await storage_import.queue_prefix_import(
        db, project_id, batch_id=payload.upload_batch_id, prefix=payload.prefix,
        copyright_policy=payload.copyright_policy, user=user, bucket=bucket,
        broadcaster=payload.broadcaster, program_name=payload.program_name,
        transcode_profile=payload.transcode_profile,
    )
    return StoragePrefixImportResult(batch_id=batch.batch_id, import_status=batch.import_status)
