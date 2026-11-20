"""샷 검출 어댑터가 엔진 결과를 그대로 옮기는지 확인하는 테스트.

검출·병합 자체는 `reborn-transcode`의 `test_scene_split.py`가 검증한다.
원본 바이트를 읽는 일은 워커가 맡으므로 그 테스트도 워커 쪽에 있다.
이 파일은 어댑터의 판단만 확인한다. 하지 않는 능력을 선언하지 않는지 확인한다.
엔진 결과를 계약 타입으로 그대로 옮기는지 확인한다.
"""
from pathlib import Path

import pytest
from api_gateway.services.analysis import scene_detect


def _payload(n: int, *, raw: int | None = None, rule: str = "샷 경계 · 최대 40초 · 쉼 자리 우선") -> dict:
    """엔진 응답 형태의 스텁을 만든다. 바이너리 없이 어댑터를 검증할 때 쓴다."""
    return {
        "segments": [{"start": i * 10.0, "end": (i + 1) * 10.0} for i in range(n)],
        "raw_count": raw if raw is not None else n,
        "rule": rule,
    }


def test_declares_only_shot_boundaries():
    """샷 경계만 선언하는지 확인한다. 요약·음성 인식을 선언하면 화면이 결과가 있다고 표시한다."""
    from reborn_contracts.analysis import Capability

    assert scene_detect.INFO.capabilities == {Capability.SHOT_BOUNDARIES}
    # MI(의미 단위)와 다른 능력이다. 작업량이 10배 다르다.
    assert scene_detect.INFO.segment_kind == "shot"


def test_only_declared_fields_are_filled():
    """선언하지 않은 필드를 비우는지 확인한다. 채우면 화면이 결과가 있다고 표시한다."""
    r = scene_detect.to_result(_payload(3))
    assert len(r.segments) == 3
    assert r.speeches == [] and r.keywords == []
    assert all(s.summary is None for s in r.segments)


def test_result_says_how_many_it_merged_and_by_what_rule():
    """구간 수와 함께 병합 전 샷 수와 규칙을 돌려준다.

    47개만 표시하면 많은지 적은지 알 수 없다. 그러면 운영자가 임계값을 조정할 근거가 없다.
    """
    r = scene_detect.to_result(_payload(47, raw=412))
    assert r.basis is not None
    assert (r.basis.raw_count, r.basis.merged_count) == (412, 47)
    assert r.basis.rule == "샷 경계 · 최대 40초 · 쉼 자리 우선"


def test_empty_result_has_no_basis():
    """구간이 없으면 `basis`를 None으로 둔다. 0개를 병합했다는 설명은 의미가 없다."""
    assert scene_detect.to_result({"segments": []}).basis is None


def test_vendor_cut_segments_have_no_basis():
    """벤더가 자른 구간이면 `basis`를 `None`으로 둔다. `None`은 병합 전 샷을 세지 않았다는 뜻이다.

    0으로 두면 화면이 "샷 0개를 39개로 묶었다"는 틀린 문장을 표시한다.
    """
    from api_gateway.services.analysis import dummy

    r = dummy.analyze(dummy.FULL, video_id="v", duration_seconds=300.0)
    assert r.segments and r.basis is None


def test_analyze_passes_the_threshold_through():
    """호출자가 준 임계값을 엔진에 그대로 넘기는지 확인한다. 어댑터가 자기 값으로 덮으면 프리셋 설정이 적용되지 않는다."""
    seen: dict = {}

    def _spy(uri, duration, threshold):
        """전달된 인자를 기록하고 스텁 결과를 돌려준다."""
        seen.update(uri=uri, duration=duration, threshold=threshold)
        return _payload(2)

    scene_detect.analyze(
        path=Path("/tmp/v.mp4"), duration_seconds=120.0, threshold=0.45, split=_spy
    )
    assert seen == {"uri": "/tmp/v.mp4", "duration": 120.0, "threshold": 0.45}


def test_failure_is_not_an_empty_result():
    """엔진 실패를 빈 결과로 바꾸지 않고 예외로 올리는지 확인한다.

    빈 결과로 바꾸면 컷이 없는 영상과 구분되지 않는다.
    그러면 그 영상 전체가 한 클립이 되고 오류 없이 잘못 잘린다.
    """
    def _boom(_uri, _duration, _threshold):
        """엔진 실패를 흉내 낸다."""
        raise RuntimeError("검출 실패: ffmpeg 죽음")

    with pytest.raises(RuntimeError):
        scene_detect.analyze(path=Path("/dev/null"), duration_seconds=30.0, split=_boom)


def test_constants_come_from_the_engine():
    """규칙 값이 엔진의 상수와 같은 객체인지 확인한다. 어댑터에서 다시 정의하지 않는다.

    값이 달라지면 화면이 표시하는 규칙과 실제로 자른 규칙이 달라진다.
    그 차이는 클립을 열어 보기 전까지 보이지 않는다.
    """
    from reborn_transcode import scene_split as engine

    assert scene_detect.MERGE_MAX_SEC is engine.MERGE_MAX_SEC
    assert scene_detect.SILENCE_MIN_SEC is engine.SILENCE_MIN_SEC
    assert scene_detect.DEFAULT_THRESHOLD is engine.DEFAULT_THRESHOLD
