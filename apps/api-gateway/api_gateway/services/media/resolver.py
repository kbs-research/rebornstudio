"""용도별 매체 선택 규칙.

파이프라인 입력과 재생 매체는 이 모듈에서만 고른다.
호출하는 코드는 `source_uri`를 직접 읽지 않는다. 업로드·probe처럼 원본 자체가 대상인 코드만 예외다.
호출하는 코드마다 `analysis_uri or source_uri` 같은 식을 쓰면 단계가 늘 때 모두 고쳐야 하므로
우선순위를 이 모듈에 모은다.
"""
from __future__ import annotations

from typing import Literal, NamedTuple

from api_gateway.models import Video

# analysis_input: MI 등록에 내보내는 파일. h264 사본이 있으면 사본, 없으면 원본이다.
# transcode_input: 트랜스코딩이 읽는 원본. 원본을 보관 해제했으면 없다.
# deident_input: 비식별화가 읽는 마스킹 전 파일. 정규화된 마스터가 있으면 마스터, 없으면 원본이다.
# playback_input: 재생본(480p)을 만들 입력. 라벨러가 보는 재생본도 비식별화되도록 마스터를 우선한다.
# export_source: 내보내기 매니페스트가 가리키는 파일. 지워진 원본은 가리키지 않는다.
#   규칙은 playback_input과 같지만 납품물은 비식별화본이어야 해서 이름을 나눠 둔다.
Purpose = Literal[
    "analysis_input", "transcode_input", "deident_input", "playback_input", "export_source"
]


def media_uri(video: Video, purpose: Purpose) -> str | None:
    """용도별로 지금 유효한 매체 URI를 돌려준다. 우선순위는 이 함수에만 있다.

    비식별화 산출은 별도 URI 없이 마스터 위치에 덮어쓴다(`deident.py`의 `replaced:`).
    그래서 반환값만으로는 마스킹 여부를 알 수 없고, 이 함수도 마스킹 여부로 분기하지 않는다.
    마스킹 여부는 `deident_status`에 있고, 판정은 `video._unmasked_expr`과 MI 게이트가 한다.
    """
    if purpose == "analysis_input":
        return video.analysis_uri or video.source_uri
    if purpose == "transcode_input":
        return None if video.source_retained is False else video.source_uri
    if purpose == "deident_input":
        # transcode가 deident보다 먼저 돌므로 이 시점의 마스터는 정규화됐고 마스킹 전이다.
        # 원본을 읽으면 수 GB의 4K·AV1 원본을 다시 인코딩하고, 그 산출이 마스터를 덮어써
        # 규격 밖 파일이 분석본 위치에 놓여 MI로 간다.
        from api_gateway.services.media.transcode import master_bucket, master_key
        detail = (video.extra or {}).get("transcode") or {}
        if detail.get("master_key"):
            return f"s3://{master_bucket(video)}/{master_key(video.video_id)}"
        # 지워진 객체를 presign하면 잡이 다운로드 단계에서야 실패한다.
        # 그래서 원본을 쓸 때는 보관 해제 여부를 확인한다.
        # `transcode_input`·`playback_input`도 같은 규칙이다.
        return None if video.source_retained is False else video.source_uri
    if purpose == "export_source":
        # `playback_input`과 규칙이 같아 위임한다.
        return media_uri(video, "playback_input")
    if purpose == "playback_input":
        from api_gateway.services.media.transcode import master_bucket, master_key
        detail = (video.extra or {}).get("transcode") or {}
        # 마스터는 트랜스코딩(`extra["transcode"]`에 기록)이나 비식별화가 만든다.
        # 비식별화(`deident.complete`)는 기록 없이 산출을 `master_key` 위치로 옮긴다.
        # 이 복사가 done의 조건이다.
        # 주의: `extra["transcode"]`만 보면 트랜스코딩을 건너뛴 영상이 원본으로 폴백해
        # 비식별화를 켰는데도 마스킹 전 원본이 재생본과 납품물(`export_source`)로 나간다.
        if detail.get("master_key") or video.deident_status == "done":
            return f"s3://{master_bucket(video)}/{master_key(video.video_id)}"
        return None if video.source_retained is False else video.source_uri
    raise ValueError(f"unknown purpose: {purpose}")


class PlaybackSource(NamedTuple):
    """재생 매체. presign에 필요한 (bucket, key)와 화면·디버깅용 variant 라벨을 담는다."""

    bucket: str
    key: str
    variant: str  # "playback_480p" | "original"


def playback_source(video: Video, *, allow_original: bool = True) -> PlaybackSource | None:
    """화면에서 재생할 매체를 고른다. 480p 재생본이 있으면 재생본, 없으면 원본이다.

    존재하는 파일만 가리킨다.
    재생본 키가 불완전하면 원본이 남아 있을 때만 원본으로 폴백한다.
    가리킬 파일이 없으면 None을 돌려주고, 호출자가 상태 오류로 처리한다.

    Args:
        video: 재생할 영상.
        allow_original: 거짓이면 원본으로 폴백하지 않는다. 마스킹 전 원본을 볼 수 없는
            사용자에게 재생본만 주려는 호출자가 거짓을 넘긴다.
    """
    detail = (video.extra or {}).get("transcode") or {}
    if detail.get("playback_key") and detail.get("bucket"):
        return PlaybackSource(detail["bucket"], detail["playback_key"], "playback_480p")
    if not allow_original:
        return None
    # 원본을 보관하지 않는 프로젝트는 준비가 끝나면 원본을 지운다.
    # 그 URI로 presign하면 403·404가 난다.
    # 그래서 원본이 남아 있을 때만 폴백하고, 없으면 화면이 "재생본 준비 중"을 표시하게 한다.
    # `is False`만 삭제로 읽는다. None은 보관 여부가 기록되지 않은 값이다.
    if not video.source_uri or video.source_retained is False:
        return None
    # scheme 허용 목록은 video/keys.py의 `_parse_s3_uri`를 기준으로 맞춘다.
    # 다른 scheme까지 파싱하면 엉뚱한 bucket으로 presign 요청이 나간다.
    for scheme in ("s3://", "ncp://"):
        if video.source_uri.startswith(scheme):
            bucket, _, key = video.source_uri[len(scheme):].partition("/")
            if bucket and key:
                return PlaybackSource(bucket, key, "original")
    return None
