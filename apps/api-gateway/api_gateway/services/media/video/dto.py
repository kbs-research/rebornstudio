"""Video 행을 `VideoDTO`로 바꾸는 변환과 라벨러용 가림."""
from typing import NamedTuple

from reborn_contracts import DispatchDeferred, VideoDTO

from api_gateway.models import Video
from api_gateway.services import error_text


class LibraryStatus(NamedTuple):
    """목록과 단건 조회(`get_one`)가 `_library_status`로 채우는 파생 상태.

    생성·수정 응답은 기본값(`_to_dto`의 lib=None)을 쓴다.
    """

    clip_count: int = 0
    pipeline_stage: str = "registered"
    ingest_bucket: str = "ready"
    # 준비 여정 축. `_library_status`가 채우고, None은 계산하지 않은 기본값이다.
    prep_stage: str | None = None
    prep_blocked_stage: str | None = None
    # 분석이 끝난 뒤의 단계별 클립 수. 화면이 '완료' 행의 다음 단계를 정할 때 쓴다.
    labeling_clips: int = 0
    reviewing_clips: int = 0
    approved_clips: int = 0
    # 삭제하면 닫힐 미완 작업 수. 위 네 수의 합과 다르다.
    # 승인은 빠지고 `rejected`(재작업 중)가 들어간다.
    open_work_items: int = 0


#: 라벨러 응답에서 비우는 필드. 작업에 쓰지 않는 운영 내부 정보다.
#: `source_uri`는 버킷 키 경로이고, `metadata`(`Video.extra`)에는 `key`·`probe`·`transcode`·
#: `deident`·`maiu_index` 같은 처리 이력이 들어 있다.
_LABELER_HIDDEN = ("source_uri", "metadata", "dispatch_deferred", "has_analysis_copy")


def _redact_for_labeler(dto: VideoDTO) -> VideoDTO:
    """라벨러용 응답에서 운영 내부 필드를 비운다. 행은 빼지 않는다.

    라벨링 워크벤치의 재생기가 이 응답의 `title`을 읽으므로 엔드포인트를 403으로 막지 않고
    필드 단위로 비운다.
    키는 남기고 값만 비워 응답 타입을 하나로 유지한다.
    라벨러 화면은 `source_uri`·`metadata`를 읽지 않는다.
    """
    return dto.model_copy(update={
        "source_uri": None, "metadata": {}, "dispatch_deferred": None, "has_analysis_copy": False,
    })


def _deferred(extra: dict | None) -> DispatchDeferred | None:
    """보류된 분석 요청의 짧은 사유·전체 사유·다음 재요청 시각을 만든다.

    보류 상태가 아니면 `None`을 돌려준다.
    """
    view = error_text.dispatch_deferred_view(extra)
    return DispatchDeferred.model_validate(view) if view else None


def _metadata(v: Video) -> dict | None:
    """영상 DTO의 `metadata`를 만든다. probe 결과에 비트레이트가 없으면 평균값을 채운 사본을 쓴다.

    probe 결과에 비트레이트가 없는 영상도 영상 상세가 비트레이트를 보여 줄 수 있게 한다.
    계산 규칙은 `probe.bit_rate`에 있다. 원본 `extra`는 바꾸지 않는다.
    """
    from api_gateway.services.media.probe import bit_rate

    extra = error_text.readable_video_extra(v.extra)
    probe = (extra or {}).get("probe")
    if not isinstance(probe, dict) or probe.get("bit_rate") is not None:
        return extra
    rate = bit_rate(probe, v)
    if rate is None:
        return extra
    return {**extra, "probe": {**probe, "bit_rate": rate}}


def _to_dto(
    v: Video,
    thumbnail_url: str | None = None,
    lib: LibraryStatus | None = None,
) -> VideoDTO:
    """Video를 VideoDTO로 바꾼다.

    `thumbnail_url`과 `lib`(클립 수·단계·반입 상태)는 목록·단건 조회가 넣는다.
    """
    st = lib or LibraryStatus()
    return VideoDTO(
        video_id=v.video_id,
        project_id=v.project_id,
        title=v.title,
        source_uri=v.source_uri,
        duration_seconds=v.duration_seconds,
        file_size_bytes=v.file_size_bytes,
        copyright_policy=v.copyright_policy,
        upload_status=v.upload_status,
        broadcaster=v.broadcaster,
        program_name=v.program_name,
        genre=v.genre,
        episode=v.episode,
        aired_at=v.aired_at,
        upload_batch_id=v.upload_batch_id,
        probe_status=v.probe_status,
        transcode_status=v.transcode_status,
        # 분석 요청 실패 사유(`dispatch_error`)를 읽을 때 화면 문구로 바꾼다.
        # 원본 `extra`는 바꾸지 않는다.
        metadata=_metadata(v),
        has_analysis_copy=bool(v.analysis_uri),
        dispatch_deferred=_deferred(v.extra),
        thumbnail_url=thumbnail_url,
        clip_count=st.clip_count,
        pipeline_stage=st.pipeline_stage,
        ingest_bucket=st.ingest_bucket,
        prep_stage=st.prep_stage,
        prep_blocked_stage=st.prep_blocked_stage,
        labeling_clips=st.labeling_clips,
        reviewing_clips=st.reviewing_clips,
        approved_clips=st.approved_clips,
        open_work_items=st.open_work_items,
        created_at=v.created_at,
    )
