"""MAIU 5단계 reconciler의 공개 import 경로. 하위 모듈에 나눠 둔 이름을 이 모듈에서 다시 내보낸다.

워커(`worker_io.tasks.run_maiu_reconciler`)·테스트·시드 스크립트는 `reconciler.<이름>`으로
부른다. 이 모듈이 그 이름을 전부 내보내므로 호출하는 쪽은 하위 모듈 구성을 알 필요가 없다.
새 코드도 이 경로를 쓴다. 하위 모듈 구성과 의존 방향은 같은 폴더 README에 있다.

주의: 테스트에서 `monkeypatch.setattr`로 이름을 바꿀 때는 그 이름을 실제로 조회하는 하위 모듈의
속성을 바꾼다. 이 모듈의 속성을 바꿔도 하위 모듈의 전역 이름은 바뀌지 않는다.
"""
from __future__ import annotations

from api_gateway.services.maiu.reconciler.common import _BATCH, _job_target
from api_gateway.services.maiu.reconciler.dto import (
    _group_segments_by_scene,
    _merge_keywords,
)
from api_gateway.services.maiu.reconciler.errors import (
    _BASE_BACKOFF_S,
    _MAX_BACKOFF_S,
    _STAGE_BY_CALLER,
    _on_stage_error,
    log,
)
from api_gateway.services.maiu.reconciler.ingest import (
    _apply_import_metadata,
    _attach_scene_summaries,
    _create_clips_from_summary,
)
from api_gateway.services.maiu.reconciler.jobs import _RECONCILER_LOCK, _run_stages, run_once
from api_gateway.services.maiu.reconciler.stages import (
    _MAX_RETRY,
    _SUMMARY_FAIL,
    _SUMMARY_OK,
    _analysis_id,
    _lock_batch,
    _resume_status,
    bulk_check_analyses,
    bulk_check_registrations,
    bulk_check_summaries,
    bulk_poll_analyses,
    bulk_poll_registrations,
    bulk_poll_scene_summaries,
    promote_failures_to_deferred,
    retry_deferred,
    sweep_stale_in_flight,
)

__all__ = [
    "_BASE_BACKOFF_S",
    "_BATCH",
    "_MAX_BACKOFF_S",
    "_MAX_RETRY",
    "_RECONCILER_LOCK",
    "_STAGE_BY_CALLER",
    "_SUMMARY_FAIL",
    "_SUMMARY_OK",
    "_analysis_id",
    "_apply_import_metadata",
    "_attach_scene_summaries",
    "_create_clips_from_summary",
    "_group_segments_by_scene",
    "_job_target",
    "_lock_batch",
    "_merge_keywords",
    "_on_stage_error",
    "_resume_status",
    "_run_stages",
    "bulk_check_analyses",
    "bulk_check_registrations",
    "bulk_check_summaries",
    "bulk_poll_analyses",
    "bulk_poll_registrations",
    "bulk_poll_scene_summaries",
    "log",
    "promote_failures_to_deferred",
    "retry_deferred",
    "run_once",
    "sweep_stale_in_flight",
]
