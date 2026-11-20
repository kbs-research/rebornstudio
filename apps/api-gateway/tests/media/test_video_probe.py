"""ffprobe 규격 게이트 테스트. check_spec 규칙과 변환 대상 판정을 확인한다.

ffprobe는 mock으로 바꾼다. CI에는 ffmpeg가 없다. reborn_transcode 단위 테스트도 같은 방식이다.
확인 대상은 규격 판정 규칙(`check_spec`)과 변환 사유(`needs_transcode`)다.
"""
from __future__ import annotations

import pytest
from api_gateway.config import settings
from api_gateway.services.media.probe import check_spec

_OK = {
    "duration_seconds": 600.0,
    "size_bytes": 1024,
    "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
    "video_codec": "h264",
    "width": 1920,
    "height": 1080,
    "fps": 30.0,
}


def test_spec_passes_normal_video():
    """정상 영상은 위반 목록이 비어 있다."""
    assert check_spec(_OK) == []


def test_spec_rejects_oversized():
    """크기 초과 파일을 거부한다.

    MI는 시간당 과금이라 초과 파일을 보내기 전에 막는다.
    """
    v = check_spec({**_OK, "size_bytes": settings.probe_max_bytes + 1})
    assert len(v) == 1 and v[0].startswith("size_exceeds_limit")


def test_spec_rejects_too_long():
    """길이 초과 파일을 거부한다."""
    v = check_spec({**_OK, "duration_seconds": settings.probe_max_duration_seconds + 1})
    assert len(v) == 1 and v[0].startswith("duration_exceeds_limit")


def test_spec_rejects_codec_not_in_whitelist():
    """화이트리스트에 없는 코덱을 거부한다."""
    v = check_spec({**_OK, "video_codec": "wmv3"})
    assert len(v) == 1 and v[0].startswith("codec_not_allowed")


def test_spec_accepts_whitelisted_codec_case_insensitively():
    """화이트리스트 코덱은 대소문자와 관계없이 통과한다.

    ffprobe 출력의 대소문자가 판정을 바꾸지 않아야 한다.
    """
    assert check_spec({**_OK, "video_codec": "H264"}) == []


@pytest.mark.parametrize("field", ["duration_seconds", "size_bytes", "video_codec"])
def test_spec_treats_unknown_as_not_a_violation(field):
    """값이 없는 필드는 위반으로 판정하지 않는다. 컨테이너가 제공하지 않은 값으로 반려하지 않는다."""
    assert check_spec({**_OK, field: None}) == []


def test_spec_boundary_is_inclusive():
    """한계값 이하는 통과한다. off-by-one으로 정상 파일을 막지 않는다."""
    assert check_spec({**_OK, "size_bytes": settings.probe_max_bytes}) == []
    assert check_spec({**_OK, "duration_seconds": settings.probe_max_duration_seconds}) == []


def test_spec_reports_all_violations_together():
    """위반을 모두 한 번에 돌려준다. 운영자가 하나씩 고치고 다시 걸리는 왕복을 줄인다."""
    v = check_spec({
        **_OK,
        "size_bytes": settings.probe_max_bytes + 1,
        "duration_seconds": settings.probe_max_duration_seconds + 1,
        "video_codec": "wmv3",
    })
    assert len(v) == 3


def test_spec_skips_codec_check_when_whitelist_empty(monkeypatch):
    """화이트리스트를 비우면 코덱 검사를 끈다. 설정으로 규칙을 완화하는 수단이다."""
    monkeypatch.setattr(settings, "probe_codec_whitelist", "")
    assert check_spec({**_OK, "video_codec": "anything"}) == []


def test_check_spec_mi_upload_table():
    """MI 업로드 규격표와 대조한다. 컨테이너와 해상도의 상하한을 확인한다.

    표에 있는 항목만 검사한다.
    컨테이너는 MP4/MKV/MOV/WEBM, 해상도는 360p~1080p, 크기는 10GB, 길이는 6시간이다.
    FPS는 표에 "10 FPS 권장"으로만 있어 거부 사유로 쓰지 않는다.
    권장을 제한으로 바꾸면 정상 영상이 막힌다.
    """
    from api_gateway.services.media.probe import check_spec

    ok = {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "video_codec": "h264",
          "height": 1080, "duration_seconds": 100.0, "size_bytes": 1000}
    assert check_spec(ok) == []

    # ffprobe는 컨테이너 이름을 여러 개 돌려준다. 하나만 맞아도 통과해야 한다.
    assert check_spec({**ok, "format_name": "matroska,webm"}) == []

    # avi는 표에 없어서 위반이다. 다만 변환이 mp4로 고칠 수 있어 구제 대상이다.
    v = check_spec({**ok, "format_name": "avi"})
    assert v and v[0].startswith("container_not_allowed")

    # 해상도는 초과와 미달의 사유가 다르다. 초과는 변환이 낮추지만 미달은 고칠 수 없다.
    assert check_spec({**ok, "height": 2160})[0].startswith("resolution_too_high")
    assert check_spec({**ok, "height": 240})[0].startswith("resolution_too_low")

    # 값이 없는 필드는 위반으로 판정하지 않는다.
    assert check_spec({"video_codec": "h264"}) == []

    # 높은 fps는 권장 기준을 넘을 뿐이라 거부하지 않는다.
    assert check_spec({**ok, "fps": 60.0}) == []


def test_redeemable_violations_are_transcode_fixable():
    """구제 목록은 변환이 실제로 고치는 위반만 담는다.

    산출은 h264, mp4, 720p로 고정이다.
    크기·길이 초과는 자르는 단계가 없어서 구제 목록에서 뺀다.
    해상도 미달도 업스케일로 정보를 만들 수 없어 뺀다.
    이런 위반이 섞이면 고칠 수 없는 영상을 변환 큐에 넣어 CPU만 쓴다.
    """
    from api_gateway.services.media.probe import REDEEMABLE_VIOLATIONS

    assert set(REDEEMABLE_VIOLATIONS) == {
        "codec_not_allowed", "container_not_allowed", "resolution_too_high",
    }


def test_needs_transcode_covers_container():
    """컨테이너만 맞지 않는 영상도 변환 대상으로 판정한다.

    이 사유가 없으면 `h264 · 720p · avi`는 구제 판정(REDEEMABLE)을 받고도 변환 사유가 없다.
    그러면 변환 없이 그대로 거부된다.
    """
    from api_gateway.services.media.transcode import needs_transcode

    # 컨테이너 사유가 있으면 재생본 사유를 덧붙이지 않는다. 재생본은 변환 뒤 publish가 만든다.
    assert needs_transcode(
        {"video_codec": "h264", "height": 720, "format_name": "avi"}, "auto"
    ) == "container:avi"
    assert needs_transcode(
        {"video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"}, "auto"
    ) == "playback:720"   # 재생본 때문에 대상이다


def test_needs_transcode_covers_playback_copy():
    """720p 영상은 분석본이 필요 없어도 재생본(480p) 때문에 변환 대상으로 판정한다.

    분석 기준(h264 아님, 720p 초과)만 보면 `h264 · 720p`를 건너뛰어 480p 재생본이 만들어지지 않는다.
    그러면 라벨러가 원본(수 GB)을 그대로 스트리밍한다.
    """
    from api_gateway.services.media.transcode import needs_transcode

    h264 = {"video_codec": "h264", "format_name": "mov,mp4,m4a"}

    # 분석본이 필요하면 그 사유가 우선이다. 재생본은 변환 뒤 publish가 만든다.
    assert needs_transcode({**h264, "height": 1080}, "auto") == "height:1080"
    # 분석본은 필요 없지만 재생본은 필요하다.
    assert needs_transcode({**h264, "height": 720}, "auto") == "playback:720"
    # 재생본 규격 이하면 만들 것이 없다.
    assert needs_transcode({**h264, "height": 480}, "auto") is None
    # 높이 값이 없으면 대상으로 판정하지 않는다. probe 실패가 CPU 낭비로 이어지지 않게 한다.
    assert needs_transcode({**h264}, "auto") is None
    # profile=never는 어떤 사유도 무시한다.
    assert needs_transcode({**h264, "height": 720}, "never") is None
