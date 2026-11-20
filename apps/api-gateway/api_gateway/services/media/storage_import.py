"""폴더 단위 등록. 서버가 NCP prefix를 순회하며 영상을 등록하는 비동기 작업.

API(`queue_prefix_import`)는 묶음 행에 작업 사양만 기록한다.
worker beat(`run_pending_imports`, 10초 주기)가 서버에서 버킷을 순회하며
페이지 단위로 등록하고 커밋한다. 묶음 행의 영상 수가 진행 표시다.
브라우저가 순회하면 파일이 수만 개인 폴더에서 수백 번 왕복하고, 탭을 닫거나 중간에 실패하면
처음부터 다시 해야 해서 서버가 순회한다.

복구 규약은 다음과 같다.
페이지마다 continuation token을 spec에 커밋한다.
worker가 비정상 종료돼도 다음 beat가 'running' 행을 이어받아 끊긴 페이지부터 다시 시작한다.
등록과 토큰 전진은 한 커밋이라서 끊긴 페이지는 커밋되지 않고 처음부터 다시 등록된다.
일시 오류(스로틀·순단)는 재시도 횟수에 세지 않고, 다음 beat가 끊긴 페이지부터 이어받는다.
영구 오류만 세어 `_MAX_ERRORS`번에 닿으면 failed로 확정한다.

같은 프로젝트에 이미 등록한 원본(삭제되지 않은 같은 URI)은 건너뛰고, 건너뛴 수를
spec의 `skipped_existing`에 누적한다. 같은 폴더를 다시 등록하면 그사이 추가된 파일만 등록된다.
"""
from __future__ import annotations

import logging
from functools import partial
from uuid import UUID

import anyio
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from api_gateway.models import IngestBatch, Project, User
from api_gateway.services import audit, authz, error_text, platform_credentials
from api_gateway.services.joblock import single_runner
from api_gateway.services.media import storage
from api_gateway.services.media import video as video_service
from api_gateway.services.transient import is_transient_boto

log = logging.getLogger(__name__)

# 한 번에 순회하는 페이지 크기. 서버 간 통신이라 왕복이 적도록 list_objects_v2 상한(1000)을 쓴다.
_PAGE = 1000
# 영구 오류 재시도 상한. 일시 오류(transient)는 세지 않는다.
# 일시 오류까지 세면 NCP 스로틀이 세 페이지 연속으로 나올 때 폴더 등록이 영구 실패로 확정된다.
_MAX_ERRORS = 3
# 단일 실행 advisory lock 키. 잡 러너는 플랫폼 전체에서 한 번에 하나만 돈다.
_ADVISORY_KEY = 0x52425349  # 'RBSI'. 락 획득·해제 규약은 services/joblock.py에 있다


async def queue_prefix_import(
    db: AsyncSession, project_id: UUID, *, batch_id: UUID, prefix: str,
    copyright_policy: str, user: User,
    broadcaster: str | None = None, program_name: str | None = None,
    transcode_profile: str = "auto",
    bucket: str | None = None,
) -> IngestBatch:
    """폴더 단위 등록 작업을 큐에 넣는다. 묶음 행을 만들고 import_spec을 기록한다.

    실제 등록은 worker가 한다.
    미리 알 수 있는 실패를 바로 돌려주려고 큐에 넣기 전에 prefix에서 1건만 조회해
    접근할 수 있는지와 파일이 있는지 확인한다.

    Raises:
        HTTPException: 프로젝트가 없는 경우(404 `project_not_found`), 권한이 없는 경우(403),
            batch_id가 이미 있는 경우(409 `batch_id_conflict`),
            스토리지 조회가 실패한 경우(503 `storage_unavailable`),
            폴더가 빈 경우(400 `empty_prefix`).
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    await authz.require_project_admin(db, project_id, user)

    prefix = prefix.lstrip("/")
    # 프로젝트가 고른 저장소 연결의 접근 키로 읽는다. 버킷을 고르지 않았으면 프로젝트 버킷을 쓴다.
    store = storage.for_project(project.settings)
    bucket = bucket or store.bucket
    if await db.get(IngestBatch, batch_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "batch_id_conflict")

    try:
        peek = await anyio.to_thread.run_sync(
            partial(storage.browse_bucket, prefix=prefix, max_keys=1, recursive=True,
                    bucket=bucket, store=store)
        )
    except Exception as exc:
        # 원문 예외(엔드포인트·RequestId)는 로그에만 남기고 화면에는 실패한 동작만 알린다.
        # 스토리지 종류는 프리셋이 정하므로 오류 코드에 벤더 이름을 넣지 않는다.
        log.warning("폴더째 등록 사전 확인 실패 prefix=%s: %s", prefix, exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable") from exc
    if not peek["files"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_prefix")

    batch = IngestBatch(
        batch_id=batch_id, project_id=project_id, created_by=user.user_id, source="ncp",
        # 사람이 알아볼 기본 이름으로 폴더명을 쓴다. 인라인 편집으로 언제든 바꿀 수 있다.
        label=(prefix.rstrip("/").rsplit("/", 1)[-1] or prefix or "버킷 루트"),
        import_spec={
            "prefix": prefix,
            # 순회할 버킷. worker가 페이지마다 다시 읽는다.
            "bucket": bucket,
            "copyright_policy": copyright_policy,
            "broadcaster": broadcaster,
            "program_name": program_name,
            "transcode_profile": transcode_profile,
        },
        import_status="pending",
    )
    db.add(batch)
    audit.emit(
        db, event_type="storage.prefix_import_queued", target_kind="ingest_batch",
        target_id=batch_id, actor_user_id=user.user_id, project_id=project_id,
        payload={"prefix": prefix, "copyright_policy": copyright_policy},
    )
    await db.commit()
    return batch


async def run_pending_imports(db: AsyncSession) -> dict[str, int]:
    """pending·running 폴더 등록 작업을 이어서 처리한다. 10초 주기 beat 진입점이다.

    advisory try-lock으로 한 번에 하나만 실행한다. 다른 worker가 실행 중이면 바로 반환한다.
    잡은 페이지마다 커밋하므로 worker가 비정상 종료되면 행이 running으로 남는다.
    lock을 얻었는데 running인 행은 중단된 실행이므로 spec의 token부터 다시 시작한다.

    Returns:
        dict: 관찰용 수치. `batches`는 처리한 묶음 수, `registered`는 등록한 영상 수.
    """
    async with single_runner(_ADVISORY_KEY) as got:
        if not got:
            return {"batches": 0, "registered": 0}
        batches = list((await db.scalars(
            select(IngestBatch)
            .where(IngestBatch.import_status.in_(("pending", "running")))
            .order_by(IngestBatch.created_at)
        )).all())
        total = 0
        for batch in batches:
            total += await _run_one(db, batch)
        return {"batches": len(batches), "registered": total}


async def _batch_has_videos(db: AsyncSession, batch: IngestBatch) -> bool:
    """묶음에 영상이 하나라도 있으면 True를 돌려준다. 빈 묶음을 지우기 전에 확인한다."""
    from api_gateway.models import Video

    return bool(await db.scalar(
        select(Video.video_id).where(Video.upload_batch_id == batch.batch_id).limit(1)
    ))


async def _run_one(db: AsyncSession, batch: IngestBatch) -> int:
    """묶음 하나를 끝까지(또는 오류가 날 때까지) 순회하고 등록한 영상 수를 돌려준다.

    페이지 단위로 등록하고 커밋하므로 화면에 진행 상황이 반영된다.
    """
    spec = dict(batch.import_spec or {})
    prefix: str = spec.get("prefix", "")
    project = await db.get(Project, batch.project_id)
    project_settings = project.settings if project else None
    token: str | None = spec.get("next_token")
    batch.import_status = "running"
    await db.commit()

    registered = 0
    while True:
        try:
            # 프로젝트가 고른 저장소 연결의 접근 키로 읽는다. 읽지 못하면 아래 예외 처리에서 센다.
            await platform_credentials.ensure_storage_loaded(
                db, platform_credentials.storage_target_of(project_settings))
            store = storage.for_project(project_settings)
            page = await anyio.to_thread.run_sync(partial(
                storage.browse_bucket,
                prefix=prefix, max_keys=_PAGE, recursive=True, token=token,
                bucket=spec.get("bucket"), store=store,
            ))
            result = await video_service.register_keys(
                db, batch.project_id, [f["key"] for f in page["files"]],
                spec.get("copyright_policy", "pre_cleared"),
                created_by=batch.created_by,
                upload_batch_id=batch.batch_id,
                broadcaster=spec.get("broadcaster"),
                program_name=spec.get("program_name"),
                transcode_profile=spec.get("transcode_profile", "auto"),
                bucket=spec.get("bucket"),
                skip_existing=True,
            )
        except Exception as exc:
            # 등록 도중 DB 쓰기가 실패하면 세션이 실패 상태로 남아 아래 기록의 커밋까지 실패한다.
            # 그러면 오류 횟수가 남지 않아 묶음이 상한에 닿지 못하고 매 주기 같은 실패를 되풀이한다.
            # 그래서 되돌린 뒤 묶음 행을 다시 읽어 기록한다.
            # 끊긴 페이지의 미커밋 행도 함께 버려진다.
            await db.rollback()
            await db.refresh(batch)
            if is_transient_boto(exc):
                # 일시 오류는 영구 오류 횟수에 세지 않고 행을 running으로 두어
                # 다음 beat가 spec의 token부터 이어받게 한다.
                # 끊긴 페이지는 커밋되지 않았으므로 다시 등록해도 중복되지 않는다.
                # `transient_count`는 관찰용이고 판정에 쓰지 않는다.
                spec["transient_count"] = int(spec.get("transient_count", 0)) + 1
                batch.import_spec = spec
                flag_modified(batch, "import_spec")
                log.warning("폴더째 등록 일시 오류 — 다음 주기에 재개 batch=%s: %s",
                            batch.batch_id, exc)
                await db.commit()
                return registered
            errors = int(spec.get("error_count", 0)) + 1
            spec["error_count"] = errors
            batch.import_spec = spec
            flag_modified(batch, "import_spec")
            if errors >= _MAX_ERRORS:
                batch.import_status = "failed"
                # 화면(Job 행 툴팁)에 표시되는 사유라 운영자용 문구로 쓰고,
                # 원인 추적에 필요한 원문은 감사 이벤트에 남긴다.
                batch.import_error = error_text.operator_text(exc) or "등록 작업이 실패했습니다"
                audit.emit(
                    db, event_type="storage.prefix_import_failed", target_kind="ingest_batch",
                    target_id=batch.batch_id, actor_user_id=batch.created_by,
                    project_id=batch.project_id,
                    payload={"prefix": prefix, "registered": registered, "errors": errors,
                             "raw": error_text.raw_detail(exc, 500)},
                )
                log.exception("prefix import failed: batch=%s", batch.batch_id)
            await db.commit()
            return registered

        registered += result["registered"]
        # 이미 등록돼 건너뛴 파일 수를 묶음에 누적한다. 새 영상이 0건인 재등록도 이유가 남는다.
        skipped = int(spec.get("skipped_existing", 0)) + int(result["skipped"])
        spec["skipped_existing"] = skipped
        token = page.get("next_token")
        spec["next_token"] = token
        spec.pop("error_count", None)  # 페이지가 성공하면 장애가 풀린 것이므로 재시도 횟수를 지운다
        batch.import_spec = spec
        flag_modified(batch, "import_spec")
        if not token:
            batch.import_status = "done"
            batch.import_error = None
            # 새로 등록한 영상도 건너뛴 파일도 없으면 이력에 빈 행이 쌓이지 않게 묶음을 지운다.
            # 건너뛴 파일이 있는 묶음은 남긴다.
            # 지우면 등록 요청이 아무 결과 없이 사라진 것으로 보인다.
            if registered == 0 and skipped == 0 and not await _batch_has_videos(db, batch):
                log.info("빈 묶음 정리 — 새로 등록된 영상 없음: prefix=%s", prefix)
                audit.emit(
                    db, event_type="storage.prefix_import_empty", target_kind="ingest_batch",
                    target_id=batch.batch_id, actor_user_id=batch.created_by,
                    project_id=batch.project_id, payload={"prefix": prefix},
                )
                await db.delete(batch)
                await db.commit()
                return 0
            audit.emit(
                db, event_type="storage.prefix_import_done", target_kind="ingest_batch",
                target_id=batch.batch_id, actor_user_id=batch.created_by,
                project_id=batch.project_id,
                payload={"prefix": prefix, "registered": registered,
                         "skipped_existing": skipped},
            )
            await db.commit()
            return registered
        await db.commit()  # 페이지마다 커밋한다. 묶음 행의 영상 수가 진행 표시다
