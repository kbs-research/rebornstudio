"""하위 리소스 모듈의 경로를 정해진 순서로 한 `router`에 모은 영상 API 라우터.

영상 등록·조회·수정·삭제, 업로드, 반입 묶음, 클립 구간, 준비 단계 재시도를 다룬다.
`main.py`는 이 패키지의 `router` 하나만 include한다.

등록 순서가 OpenAPI 문서의 경로 순서를 정한다.
같은 경로를 여러 라우트가 받으면 먼저 등록한 라우트가 매칭된다.
하위 라우터를 차례로 include하면 모듈별로 묶여 순서가 바뀌므로 `_ORDER`로 조립한다.
하위 모듈에 경로를 더하면 `_ORDER`에도 넣는다. 빠뜨리면 import할 때 `RuntimeError`가 난다.
"""
from fastapi import APIRouter
from fastapi.routing import APIRoute

from api_gateway.api.v1.videos import (
    artifacts,
    catalog,
    clips,
    ingest_batches,
    prep,
    upload,
)

# 경로를 모을 하위 라우터 모듈.
_SUBMODULES = (catalog, upload, ingest_batches, prep, artifacts, clips)

#: 경로를 등록할 순서. 하위 모듈의 엔드포인트 함수를 빠짐없이 한 번씩 적는다.
_ORDER = (
    catalog.create_video,
    catalog.list_videos,
    catalog.video_bucket_counts,
    upload.video_upload_limits,
    catalog.select_videos,
    ingest_batches.list_ingest_batches,
    ingest_batches.get_ingest_job,
    ingest_batches.update_ingest_batch,
    ingest_batches.download_meta_template,
    ingest_batches.preview_meta_import,
    ingest_batches.apply_meta_import,
    catalog.get_video,
    catalog.delete_video,
    prep.get_prep_timeline,
    artifacts.purge_video_analysis,
    catalog.get_source_url,
    upload.initiate_multipart,
    upload.sign_upload_parts,
    upload.complete_upload,
    catalog.update_video,
    clips.create_clip_manually,
    clips.list_clips_for_project,
    clips.count_clips_for_project,
    clips.get_clip,
    clips.list_clips_for_video,
    prep.retry_deident,
    prep.skip_deident,
    prep.retry_scene_split,
    prep.retry_transcode,
    prep.retry_thumbnails,
    prep.reset_video_probe,
)


def _assemble() -> APIRouter:
    """하위 라우터의 경로를 `_ORDER` 순서로 한 라우터에 모은다.

    Raises:
        RuntimeError: 하위 라우터의 엔드포인트와 `_ORDER`가 한 번씩 짝지어지지 않는 경우.
    """
    routes = [r for m in _SUBMODULES for r in m.router.routes if isinstance(r, APIRoute)]
    by_endpoint = {r.endpoint: r for r in routes}
    if len(by_endpoint) != len(routes) or len(set(_ORDER)) != len(_ORDER):
        raise RuntimeError("videos: 엔드포인트가 두 번 등록됐거나 _ORDER에 두 번 적혔습니다")
    missing = [r.endpoint.__name__ for r in routes if r.endpoint not in _ORDER]
    unknown = [f.__name__ for f in _ORDER if f not in by_endpoint]
    if missing or unknown:
        raise RuntimeError(f"videos: _ORDER 불일치 — 빠짐 {missing} · 경로 없음 {unknown}")
    assembled = APIRouter()
    assembled.routes.extend(by_endpoint[f] for f in _ORDER)
    return assembled


router = _assemble()

__all__ = ["router"]
