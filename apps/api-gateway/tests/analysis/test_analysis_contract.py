"""분석 어댑터 계약 테스트. 모든 어댑터가 같은 테스트를 통과해야 한다.

어댑터마다 테스트를 따로 쓰면 기준이 서로 달라진다.
그래서 같은 테스트를 어댑터만 바꿔 가며 돌린다.
새 클라우드 어댑터는 이 테스트를 통과하면 계약을 지킨다.

계약은 선언한 능력만 검사한다.
선언하지 않은 능력의 결과가 비어 있으면 정상이다.
선언한 능력의 결과가 비어 있으면 결함이다.
"""
from pathlib import Path

import pytest
from api_gateway.services.analysis import dummy, registry, scene_detect
from reborn_contracts.analysis import AdapterInfo, AnalysisResult, Capability

# 계약 테스트를 돌릴 어댑터 목록. 새 어댑터를 붙이면 여기에 더한다.
#
# `scenedetect`는 구조가 다른 두 번째 구현이다.
# 더미는 계약을 작성한 방식대로 만들어져 계약을 항상 만족한다.
# 그래서 구조가 다른 구현에도 계약이 맞는지는 `scenedetect`로 확인한다.
ADAPTERS = [dummy.FULL, dummy.SEGMENTS_ONLY, dummy.EMPTY, scene_detect.INFO]

_VIDEO = "66bacfa3-e8cb-4d63-afff-0716d968de16"
_DURATION = 2366.733


def _stub_split(_uri, duration: float, _threshold) -> dict:
    """바이너리 없이 계약을 검증하도록 장면 분할 엔진 결과를 흉내 낸다.

    계약 테스트 결과가 `ffmpeg` 설치 여부에 따라 달라지면 테스트가 계약 준수 대신 환경 구성을 확인하게 된다.
    실제 FFmpeg 경로는 `reborn-transcode`의 테스트가 검사한다.
    """
    from reborn_transcode import scene_split as engine

    shots = engine.to_shots([12.5, 40.0, 41.9, 300.0, 1200.0, 2360.0], duration)
    return {
        "segments": engine.merge(shots, [(299.0, 302.0), (1198.5, 1201.0)], duration),
        "raw_count": len(shots),
        "rule": engine.rule_text(),
    }


def run(info: AdapterInfo, *, video_id: str = _VIDEO, duration: float = _DURATION):
    """어댑터를 호출한다. 같은 테스트를 어댑터만 바꿔 가며 돌리는 단일 진입점이다."""
    if info.key == scene_detect.INFO.key:
        return scene_detect.analyze(
            path=Path("/dev/null"), duration_seconds=duration, split=_stub_split,
        )
    return dummy.analyze(info, video_id=video_id, duration_seconds=duration)


@pytest.mark.parametrize("info", ADAPTERS, ids=lambda i: i.key)
def test_result_is_the_domain_model(info: AdapterInfo):
    """반환값이 도메인 모델인지 확인한다. 벤더 응답 형식이 도메인 안으로 들어오지 않게 한다."""
    assert isinstance(run(info), AnalysisResult)


@pytest.mark.parametrize("info", ADAPTERS, ids=lambda i: i.key)
def test_deterministic(info: AdapterInfo):
    """같은 입력에 같은 결과를 낸다. 그렇지 않으면 화면 확인 결과가 매번 달라진다."""
    assert run(info) == run(info)


@pytest.mark.parametrize("info", ADAPTERS, ids=lambda i: i.key)
def test_only_declared_capabilities_are_filled(info: AdapterInfo):
    """선언하지 않은 능력의 필드가 비어 있는지 확인한다. 채우면 화면이 잘못 표시한다.

    선언한 능력을 채우는지는 `test_declared_capabilities_are_actually_filled`가 확인한다.
    """
    r = run(info)
    if not (info.can(Capability.SEMANTIC_SEGMENTS) or info.can(Capability.SHOT_BOUNDARIES)):
        assert r.segments == []
    if not info.can(Capability.SPEECH_TO_TEXT):
        assert r.speeches == []
    if not info.can(Capability.KEYWORDS):
        assert r.keywords == []
    if not info.can(Capability.SCENE_SUMMARY):
        assert all(s.summary is None for s in r.segments)
    if not info.can(Capability.SPEAKER_DIARIZATION):
        assert all(s.speaker_id is None for s in r.speeches)


@pytest.mark.parametrize("info", ADAPTERS, ids=lambda i: i.key)
def test_declared_capabilities_are_actually_filled(info: AdapterInfo):
    """선언한 능력의 필드를 채우는지 확인한다.

    선언만 하고 비우면 화면은 결과가 있다고 표시하지만 내용이 비어 있다.
    """
    r = run(info)
    if info.can(Capability.SEMANTIC_SEGMENTS) or info.can(Capability.SHOT_BOUNDARIES):
        assert r.segments, "구간을 선언했는데 비었다"
    if info.can(Capability.SCENE_SUMMARY):
        assert all(s.summary for s in r.segments), "요약을 선언했는데 빈 구간이 있다"
    if info.can(Capability.SPEECH_TO_TEXT):
        assert r.speeches, "STT를 선언했는데 비었다"
    if info.can(Capability.SPEAKER_DIARIZATION):
        assert all(s.speaker_id for s in r.speeches), "화자 분리를 선언했는데 없는 발화가 있다"
    if info.can(Capability.KEYWORDS):
        assert r.keywords, "키워드를 선언했는데 비었다"


@pytest.mark.parametrize("info", ADAPTERS, ids=lambda i: i.key)
def test_time_axis_is_sane(info: AdapterInfo):
    """구간은 시간 순서대로 놓이고, 서로 겹치지 않고, 영상 길이를 넘지 않는다.

    구간이 어긋나면 그 위에 얹는 라벨링·검수·납품 타임코드가 모두 어긋난다.
    """
    r = run(info)
    prev_end = 0.0
    for s in r.segments:
        assert s.end > s.start, f"길이가 0 이하: {s}"
        assert s.start >= prev_end - 1e-6, f"앞 구간과 겹친다: {s}"
        assert s.end <= _DURATION + 1e-6, f"영상 밖으로 나간다: {s}"
        prev_end = s.end
    for sp in r.speeches:
        assert sp.end >= sp.start
        assert sp.end <= _DURATION + 1e-6


@pytest.mark.parametrize("info", ADAPTERS, ids=lambda i: i.key)
def test_empty_input_gives_empty_result(info: AdapterInfo):
    """길이가 0이면 예외 없이 빈 결과를 돌려준다. 빈 결과는 정상 상태다."""
    r = run(info, duration=0.0)
    assert r.segments == []


def test_dummy_can_produce_empty_results():
    """더미가 빈 결과를 만들 수 있는지 확인한다. 화면의 '없음' 표시를 확인할 때 쓴다."""
    r = run(dummy.EMPTY)
    assert r == AnalysisResult()


def test_segment_kind_names_what_the_segments_are():
    """의미 단위 구간에 샷과 다른 `segment_kind`를 붙이는지 확인한다.

    샷과 의미 단위 구간은 같은 배열에 담기지만 작업량이 10배 다르다.
    이름이 같으면 화면이 두 구간의 차이를 표시할 수 없다.
    """
    assert dummy.FULL.segment_kind == "semantic"
    assert dummy.EMPTY.segment_kind is None


def test_dummy_matches_measured_scale():
    """더미 결과의 구간 수와 평균 길이가 MI 결과(39개, 평균 60.7초)와 같은 규모인지 확인한다.

    규모가 다르면 더미로 만든 화면이 실제 화면과 달라진다.
    """
    segs = run(dummy.FULL).segments
    avg = sum(s.end - s.start for s in segs) / len(segs)
    assert 30 <= len(segs) <= 50, len(segs)
    assert 45 <= avg <= 75, avg


def test_maiu_does_not_claim_shot_boundaries():
    """MI 어댑터가 샷 경계를 선언하지 않는지 확인한다.

    MI는 의미 단위로 자른다(영상 하나에 39개, 평균 60.7초). 샷 경계를 선언하면 화면이 틀린 정보를 표시한다.
    """
    assert not registry.MAIU.can(Capability.SHOT_BOUNDARIES)
    assert registry.MAIU.segment_kind == "semantic"


def test_unknown_adapter_is_not_filled_in_with_a_default():
    """등록되지 않은 어댑터 키에 기본값을 주지 않고 None을 돌려준다.

    다른 스택의 능력으로 표시하면 화면이 틀린 정보를 표시한다.
    """
    assert registry.get("nope") is None
