"""영상 서비스 파사드.

영상 CRUD, S3 presigned 멀티파트 업로드, soft delete, 반입 상태 판정을 다룬다.
하위 모듈의 이름을 모두 다시 내보내므로 호출하는 쪽은 `media.video`만 import한다.

주의: 테스트에서 이 모듈의 이름을 `monkeypatch.setattr`로 바꾸면 하위 모듈에는 적용되지 않는다.
하위 모듈은 자기 전역 이름으로 서로를 부른다.
그래서 그 이름을 실제로 참조하는 하위 모듈의 이름을 바꾼다(README.md).
"""
from api_gateway.services.media.video.access import (
    _load_for_admin,
    _require_member,
)
from api_gateway.services.media.video.classify import (
    _analysis_hold_case,
    _deident_expected_expr,
    _has_clips_expr,
    _has_ticket_expr,
    _ingest_bucket_case,
    _maiu_status_expr,
    _pipeline_stage_case,
    _prep_blocked_stage_case,
    _prep_stage_case,
    _prep_unfinished_expr,
    _summary_blocked_expr,
    _unmasked_expr,
)
from api_gateway.services.media.video.commands import (
    _VIDEO_EXTS,
    META_KEYS,
    absorb_media_meta,
    apply_meta,
    create,
    ensure_input_ext,
    first_missing_key,
    merge_extra,
    register_from_storage,
    register_keys,
    reset_probe,
    soft_delete,
    update,
)
from api_gateway.services.media.video.dto import (
    _LABELER_HIDDEN,
    LibraryStatus,
    _redact_for_labeler,
    _to_dto,
)
from api_gateway.services.media.video.keys import (
    _parse_s3_uri,
    _upload_key,
    archive_key,
)
from api_gateway.services.media.video.queries import (
    _SELECT_CAP,
    _first_clip_thumbnails,
    _library_status,
    _poster_url,
    bucket_counts,
    get_one,
    ingest_bucket_totals,
    issue_source_url,
    list_for_project,
    select_ids,
)
from api_gateway.services.media.video.timeline import (
    _ANALYSIS_STAGES,
    _readable_run_error,
    prep_timeline,
)
from api_gateway.services.media.video.upload import (
    _PART_SIZE,
    complete_upload,
    initiate_multipart,
    sign_upload_parts,
    upload_limits,
)

__all__ = [
    "META_KEYS",
    "LibraryStatus",
    "_ANALYSIS_STAGES",
    "_LABELER_HIDDEN",
    "_PART_SIZE",
    "_SELECT_CAP",
    "_VIDEO_EXTS",
    "_analysis_hold_case",
    "_deident_expected_expr",
    "_first_clip_thumbnails",
    "_has_clips_expr",
    "_has_ticket_expr",
    "_ingest_bucket_case",
    "_library_status",
    "_load_for_admin",
    "_maiu_status_expr",
    "_parse_s3_uri",
    "_pipeline_stage_case",
    "_poster_url",
    "_prep_blocked_stage_case",
    "_prep_stage_case",
    "_prep_unfinished_expr",
    "_readable_run_error",
    "_redact_for_labeler",
    "_require_member",
    "_summary_blocked_expr",
    "_to_dto",
    "_unmasked_expr",
    "_upload_key",
    "absorb_media_meta",
    "apply_meta",
    "archive_key",
    "bucket_counts",
    "complete_upload",
    "create",
    "ensure_input_ext",
    "first_missing_key",
    "get_one",
    "ingest_bucket_totals",
    "initiate_multipart",
    "issue_source_url",
    "list_for_project",
    "merge_extra",
    "prep_timeline",
    "register_from_storage",
    "register_keys",
    "reset_probe",
    "select_ids",
    "sign_upload_parts",
    "soft_delete",
    "update",
    "upload_limits",
]
