"""Celery 태스크. api_gateway의 async 서비스 함수를 sync 태스크로 감싼다.

Celery는 sync 태스크만 지원하므로 asyncio.run으로 감싼다.
DB 없이 도는 잡(`transcode_one`·`publish_one`·`deident_one`·`scene_split_one`)은
별도 배포 단위 `workers/worker-jobs`에 있다. 이 패키지는 그 잡을 이름으로 보내고(`dispatch`)
회신을 받는다(`callbacks`).

celery_app의 `include=["worker_io.tasks"]`가 이 패키지만 import한다. 하위 모듈을 여기서 모두
import해야 태스크가 등록된다. 빠진 모듈의 태스크는 워커에 등록되지 않아 그 이름의 메시지가 버려진다.
태스크 이름은 모듈 위치와 무관하게 `name="worker_io.tasks.<함수명>"`으로 고정한다.
큐 라우팅과 beat 스케줄이 그 이름을 가리킨다.
"""
from ..celery_app import app
from .callbacks import (
    deident_complete,
    deident_heartbeat,
    publish_complete,
    publish_heartbeat,
    scene_split_complete,
    scene_split_heartbeat,
    transcode_complete,
    transcode_heartbeat,
)
from .dispatch import (
    dispatch_deidents,
    dispatch_publishes,
    dispatch_scene_splits,
    dispatch_transcodes,
    reclaim_expired_deidents,
    reclaim_expired_publishes,
    reclaim_expired_scene_splits,
    reclaim_expired_transcodes,
)
from .qc import caption_qc_run, dispatch_caption_qc
from .sweeps import (
    check_connections,
    check_integrations,
    dispose_finished_sources,
    fill_source_checksums,
    probe_pending_videos,
    purge_audit_events,
    run_maiu_reconciler,
    run_pending_clip_thumbnails,
    run_pending_exports,
    run_pending_storage_imports,
    run_pending_video_thumbnails,
    sweep_orphan_staging,
)

__all__ = [
    "app",
    "caption_qc_run",
    "check_connections",
    "check_integrations",
    "deident_complete",
    "deident_heartbeat",
    "dispatch_caption_qc",
    "dispatch_deidents",
    "dispatch_publishes",
    "dispatch_scene_splits",
    "dispatch_transcodes",
    "dispose_finished_sources",
    "fill_source_checksums",
    "probe_pending_videos",
    "publish_complete",
    "publish_heartbeat",
    "purge_audit_events",
    "reclaim_expired_deidents",
    "reclaim_expired_publishes",
    "reclaim_expired_scene_splits",
    "reclaim_expired_transcodes",
    "run_maiu_reconciler",
    "run_pending_clip_thumbnails",
    "run_pending_exports",
    "run_pending_storage_imports",
    "run_pending_video_thumbnails",
    "scene_split_complete",
    "scene_split_heartbeat",
    "sweep_orphan_staging",
    "transcode_complete",
    "transcode_heartbeat",
]
