"""매체 결정자가 용도별 입력·재생 매체를 고르는 규칙 테스트.

DB 없이 Video ORM 객체만으로 검증한다.
결정자가 순수 함수여야 워커와 서비스 어디서 호출해도 같은 결과를 돌려준다.
"""
import pytest
from api_gateway.models import Video
from api_gateway.services.media import resolver


def _video(**kw) -> Video:
    """DB 없이 Video ORM 객체를 만들고 주어진 속성을 채운다."""
    v = Video(title="t", copyright_policy="pre_cleared", copyright_status="cleared")
    for k, val in kw.items():
        setattr(v, k, val)
    return v


def test_analysis_input_prefers_transcoded_copy():
    """분석 입력은 h264 사본(`analysis_uri`)이 있으면 사본, 없으면 원본(`source_uri`)이다."""
    v = _video(source_uri="s3://b/orig.mp4", analysis_uri="s3://b/transcoded/720p/x.mp4")
    assert resolver.media_uri(v, "analysis_input") == "s3://b/transcoded/720p/x.mp4"
    v2 = _video(source_uri="s3://b/orig.mp4")
    assert resolver.media_uri(v2, "analysis_input") == "s3://b/orig.mp4"
    assert resolver.media_uri(_video(), "analysis_input") is None


def test_deident_reads_the_master_when_transcode_made_one(monkeypatch):
    """비식별화 입력은 정규화된 마스터가 있으면 마스터를 읽는다. 원본을 다시 인코딩하지 않는다.

    `transcode` 다음에 `deident`가 돌므로 이 시점의 마스터는 정규화만 되고 아직 마스킹 전이다.
    원본을 읽으면 4K·AV1에서 GPU가 수 GB를 통째로 재인코딩한다.
    그 산출물이 마스터를 덮어써서 방금 한 정규화가 사라진다.
    원본을 입력으로 쓰면 규격 밖 산출이 분석본 위치에 놓여 MI로 간다.
    """
    from api_gateway.services.media import transcode as tc

    monkeypatch.setattr(tc, "derived_bucket", lambda _video: "derived")
    v = _video(source_uri="s3://b/orig.mp4",
               extra={"transcode": {"master_key": "work/x/analysis-720p.mp4"}})
    got = resolver.media_uri(v, "deident_input")
    assert got is not None and got.startswith("s3://derived/"), "원본을 다시 읽는다"
    assert "orig.mp4" not in got

    # 마스터가 없으면(규격 안이라 트랜스코딩을 건너뛴 경우) 원본을 읽는다.
    v2 = _video(source_uri="s3://b/orig.mp4")
    assert resolver.media_uri(v2, "deident_input") == "s3://b/orig.mp4"

    # 원본이 이미 폐기됐으면 None을 돌려준다. 다른 입력 용도와 같은 규칙이다.
    # 지워진 객체를 presign하면 잡이 다운로드 단계에서야 실패한다.
    v3 = _video(source_uri="s3://b/orig.mp4", source_retained=False)
    assert resolver.media_uri(v3, "deident_input") is None


def test_inputs_read_original_when_there_is_no_master():
    """변환 입력은 항상 원본이고, 비식별화 입력은 마스터가 없을 때만 원본이다.

    `analysis_uri`(MI 사본)가 있어도 두 입력은 사본을 쓰지 않는다.
    분석본은 MI가 소비하는 결과물이라 파이프라인 입력으로 쓰지 않는다.
    마스터가 있을 때의 비식별화 입력은 위 테스트가 검증한다.
    """
    v = _video(source_uri="s3://b/orig.mp4", analysis_uri="s3://b/copy.mp4")
    assert resolver.media_uri(v, "transcode_input") == "s3://b/orig.mp4"
    assert resolver.media_uri(v, "deident_input") == "s3://b/orig.mp4"


def test_playback_uses_the_master_deident_made(monkeypatch):
    """비식별화가 만든 마스터도 재생 입력으로 쓴다. 쓰지 않으면 원본이 라벨러에게 간다.

    마스터를 만드는 단계는 트랜스코딩과 비식별화 둘이다.
    원본이 규격 안이면 트랜스코딩을 건너뛰므로 `extra["transcode"]` 키가 생기지 않는다
    (`pipeline._needs`: "비식별화가 켜져 있으면 마스터는 비식별화가 만든다").
    재생본은 라벨러가 보는 영상이고 `export_source`도 이 결정을 따른다.
    `extra["transcode"]`만 마스터 신호로 보면 이 조합에서 원본으로 폴백한다.
    그러면 비식별화를 켰는데도 마스킹 안 된 원본이 화면과 납품물로 나간다.
    """
    from api_gateway.services.media import transcode as tc

    monkeypatch.setattr(tc, "derived_bucket", lambda _video: "derived")
    v = _video(source_uri="s3://b/orig.mp4", deident_status="done")  # transcode 건너뜀
    got = resolver.media_uri(v, "playback_input")
    assert got is not None and got.startswith("s3://derived/"), "원본으로 폴백했다"
    assert "orig.mp4" not in got, "마스킹 안 된 원본이 재생본 입력이 됐다"
    # 납품물도 같은 규칙을 물려받는다.
    assert resolver.media_uri(v, "export_source") == got

    # 비식별화를 하지 않은 영상은 원본을 재생 입력으로 쓴다.
    v2 = _video(source_uri="s3://b/orig.mp4", deident_status="skipped")
    assert resolver.media_uri(v2, "playback_input") == "s3://b/orig.mp4"


def test_unknown_purpose_is_an_error():
    """purpose 값이 잘못되면 None 대신 ValueError를 던진다.

    잘못된 입력을 고르면 엉뚱한 파일을 처리한다.
    """
    with pytest.raises(ValueError):
        resolver.media_uri(_video(), "thumbnail_input")  # type: ignore[arg-type]


def test_playback_prefers_480p_copy():
    """재생본 키가 완전할 때만 480p 사본을 쓰고, 아니면 원본을 쓴다.

    없는 사본을 지어내지 않는다.
    """
    v = _video(
        source_uri="s3://b/orig.mp4",
        extra={"transcode": {"bucket": "b", "playback_key": "transcoded/480p/x.mp4"}},
    )
    assert resolver.playback_source(v) == ("b", "transcoded/480p/x.mp4", "playback_480p")

    # 키가 불완전하면(버킷 누락) 원본으로 폴백한다. 반쪽 정보로 presign을 만들지 않는다.
    v2 = _video(source_uri="s3://b/orig.mp4", extra={"transcode": {"playback_key": "x.mp4"}})
    assert resolver.playback_source(v2) == ("b", "orig.mp4", "original")


def test_playback_scheme_whitelist_matches_parse_rule():
    """scheme 허용 목록이 `_parse_s3_uri`와 같은지 확인한다.

    다른 scheme까지 파싱하면 엉뚱한 bucket으로 presign 요청이 나간다.
    """
    assert resolver.playback_source(_video(source_uri="ncp://bk/k.mp4")) == (
        "bk", "k.mp4", "original",
    )
    assert resolver.playback_source(_video(source_uri="https://evil/k.mp4")) is None
    assert resolver.playback_source(_video(source_uri="s3://only-bucket")) is None
    assert resolver.playback_source(_video()) is None
