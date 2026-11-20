"""MI 분석 결과를 영상 메타와 클립에 반영하는 처리. 쓰기는 media가 소유한 함수를 거친다."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import AnalysisJob, Clip, Video
from api_gateway.services import audit
from api_gateway.services.analysis.registry import MAIU_KEY
from api_gateway.services.maiu.reconciler.dto import _group_segments_by_scene
from api_gateway.services.media import clip as clip_service
from api_gateway.services.media import video as video_service


async def _apply_import_metadata(
    db: AsyncSession, video: Video | None, job: AnalysisJob, raw: dict
) -> None:
    """import-status 응답의 `metaData`를 video 컬럼과 extra에 반영한다.

    `metaData`에는 resolution·durationMs·size·format이 들어 있다.
    """
    meta = (raw.get("metaData") or {}) if isinstance(raw, dict) else {}
    if not meta:
        return
    # 디버깅과 후속 처리에 쓰도록 job.payload에도 원본을 보존한다.
    job.payload = {**(job.payload or {}), "import_metadata": meta,
                   "uploadResult": raw.get("uploadResult")}
    if video is None:
        return
    # video 컬럼·extra 쓰기는 media가 소유한 함수를 거친다.
    await video_service.absorb_media_meta(
        db, video,
        duration_ms=meta.get("durationMs"),
        size_bytes=meta.get("size"),
        extra_patch={"maiu_import": {
            "resolution": meta.get("resolution"),
            "durationMs": meta.get("durationMs"),
            "size": meta.get("size"),
            "format": meta.get("format"),
            "uploadResult": raw.get("uploadResult"),
        }},
    )


async def _attach_scene_summaries(
    db: AsyncSession, job: AnalysisJob, summaries: list[dict]
) -> int:
    """scene별 description을 같은 scene clip의 auto_caption.summary에 붙인다.

    갱신한 clip 수를 돌려준다.

    `clip.extra['scene']`과 `summary['scene']`이 같은 쌍을 연결한다.
    scene 번호나 description이 없는 항목은 건너뛴다.
    그래서 scene이 None인 요약이 scene이 없는 clip 전체에 붙지 않는다.
    """
    by_scene = {
        s["scene"]: s["description"]
        for s in summaries
        if s.get("scene") is not None and s.get("description")
    }
    if not by_scene:
        return 0
    clips = (await db.scalars(select(Clip).where(Clip.video_id == job.video_id))).all()
    updated = 0
    for c in clips:
        desc = by_scene.get((c.extra or {}).get("scene"))
        if desc is None:
            continue
        c.auto_caption = {**(c.auto_caption or {}), "summary": desc}
        updated += 1
    return updated


async def _create_clips_from_summary(
    db: AsyncSession, job: AnalysisJob, summary: dict
) -> None:
    """MAIU segments로 scene 단위 clip을 INSERT하고 video.extra에 메타를 반영한다.

    clip 하나가 scene 하나다. 저장하는 값은 다음과 같다.
    `clip.auto_caption`에는 brief(발화 미리보기)와 합친 speeches 원본을 둔다.
    `clip.speakers`에는 pid 기준으로 중복을 뺀 pid·name 목록을 둔다.
    `clip.extra`에는 source, scene 번호, shots(shot별 프레임 범위·썸네일),
    scene_thumbnail_url, keywords, people을 둔다.
    `video.extra["maiu_index"]`에는 indexId·durationMs·fps·frameCount·videoUrl·
    analysis_setting·index_meta를 둔다.
    """
    video = await db.get(Video, job.video_id)
    if not video:
        return

    # video.extra에 index 메타를 병합하고 duration을 보강한다.
    # 같은 키는 최신 reconcile 결과로 덮어쓴다.
    # 쓰기는 media가 소유한 함수를 거친다.
    # 초기 rights_status 규칙도 클립 생성 규칙의 일부라 `create_scene_clip`이 정한다.
    await video_service.absorb_media_meta(
        db, video,
        duration_ms=summary.get("durationMs"),
        extra_patch={"maiu_index": {
            "indexId": summary.get("indexId"),
            "durationMs": summary.get("durationMs"),
            "fps": summary.get("fps"),
            "frameCount": summary.get("frameCount"),
            "videoUrl": summary.get("videoUrl"),
            "analysis_setting": summary.get("analysis_setting") or {},
            "index_meta": summary.get("index_meta") or {},
        }},
    )

    # 클립 경계는 반열림 구간 [start_s, end_s)다.
    # 인접 클립은 경계 시각을 공유한다(end[i]==start[i+1]).
    # 경계 프레임은 다음 클립의 첫 프레임이라서 현재 클립에는 넣지 않는다.
    # 화면의 VideoPlayer도 clipEndTime에서 같은 규칙으로 자른다.
    # 주의: 파일을 물리적으로 분할할 때도 경계 프레임은 다음 세그먼트에만 넣는다.
    #   stream-copy(-c copy) 대신 재인코딩이나 -force_key_frames로 프레임 단위로 정확히 나눈다.
    created_clips = 0
    for g in _group_segments_by_scene(summary.get("segments", [])):
        start_s = (g["startMs"] or 0) / 1000.0
        end_s = (g["endMs"] or 0) / 1000.0
        if end_s <= start_s:
            continue
        speeches = g["speeches"]
        speakers, seen = [], set()
        for s in speeches:  # 한 scene의 여러 shot에 같은 화자가 나오므로 pid 기준으로 중복을 뺀다
            pid = s.get("pid")
            if pid in seen:
                continue
            seen.add(pid)
            speakers.append({"pid": pid, "name": s.get("name")})
        # Clip INSERT는 media가 소유한다.
        # 동시 삽입의 중복 처리도 `create_scene_clip` 안에 있다.
        created_clips += 1 if await clip_service.create_scene_clip(
            db,
            video=video,
            start_seconds=start_s,
            end_seconds=end_s,
            auto_caption={
                "brief": " ".join(s.get("text", "") for s in speeches)[:200],
                "speeches": speeches,
            },
            speakers=speakers,
            scene_extra={
                # 구간을 자른 스택을 기록한다.
                # 샷 검출(`scenedetect`)·사람(`manual`)이 자른 구간과 구분하는 값이다.
                # 클립을 만드는 쪽이 기록해야 화면이 자른 주체를 추측하지 않는다.
                "source": MAIU_KEY,
                "scene": g["scene"],
                "shots": g["shots"],
                "scene_thumbnail_url": g["thumb"],
                "keywords": g["keywords"],
                "people": g["people"],
            },
        ) else 0

    # 클립이 생겼으므로 영상 티켓(MAIU 디스패치 대기 표시)을 정리한다.
    # 티켓이 남으면 디스패치 풀과 큐 집계에 계속 포함되고 사람 작업 큐에도 노출된다.
    from api_gateway.services.work import service as work_service
    await work_service.retire_video_ticket(db, job.video_id)

    # 영상 분석이 끝나 클립이 생겼음을 알리는 통합 이벤트를 남긴다. 지금은 구독자가 없다.
    # 다른 도메인이 위의 work 직접 호출 대신 이 이벤트로 연결할 수 있게 미리 발행한다.
    audit.emit(
        db, event_type="video.analysis_done", target_kind="video",
        target_id=job.video_id, project_id=job.project_id, kind="integration",
        payload={"maiu_asset_id": job.external_ref, "clip_count": created_clips},
    )
