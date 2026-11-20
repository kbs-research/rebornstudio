"""외부 서비스 예외를 운영자용 문장으로 바꾸는 번역(`services/error_text`) 테스트.

이 파일은 두 가지를 보장한다.

1. 내부 엔드포인트가 화면에 나가지 않는다.
   `naver_client` 예외 메시지는 `MAIU POST /api/v1/workspaces/…/video-assets/… → 409 {…}` 형식이다.
   그대로 저장하면 준비 단계 이슈 레인, 타임라인 시도 행, 항목 행 세 곳에 경로가 표시된다.
2. 원문은 기록에 남는다. 화면에서 빼는 것과 기록에서 지우는 것은 다르다.
   `raw_detail`은 경로·상태·본문을 그대로 돌려준다.

번역 결과를 글자 그대로 고정하지 않는다. 문구는 다듬어질 수 있다.
대신 성질을 확인한다. 경로가 없는지, 외부 서비스가 준 사유가 남는지, 결과가 비어 있지 않은지다.
"""
import pytest
from api_gateway.services.error_text import operator_reason, operator_text, raw_detail
from api_gateway.services.maiu.naver_client import MaiuTransientError

# 실제 경로와 비슷한 값을 쓴다. 짧은 가짜 경로를 쓰면 정규식이 느슨해도 테스트가 통과한다.
_PATH = "/api/v1/workspaces/example-workspace/projects/1/video-assets/asset-000"


def _leaks(text: str) -> bool:
    """운영자에게 보이면 안 되는 조각(내부 경로·약어)이 남아 있으면 True."""
    lowered = text.lower()
    return any(x in lowered for x in ("/api/", "workspaces", "maiu", "http", "traceback"))


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError(f'MAIU POST {_PATH} → 409 {{"code":"10097","message":"장면 길이 옵션 충돌"}}'),
        RuntimeError(f"MAIU POST {_PATH} → 503 upstream unavailable"),
        RuntimeError(f"MAIU GET {_PATH} → 404 not found"),
        RuntimeError(f"MAIU GET {_PATH} → 401 signature mismatch"),
        MaiuTransientError(f"MAIU GET {_PATH} → 429 {{}}", status=429),
        MaiuTransientError(f"MAIU GET {_PATH} → transport error: timed out"),
        RuntimeError("MAIU: no indexes for asset 1000001"),
        RuntimeError("MAIU register_asset: no assetId/videoId in {}"),
        RuntimeError("MAIU request_scene_summary: no sceneSummaryId in {}"),
        RuntimeError("MAIU request_transformation: no id in {}"),
        RuntimeError("NaverMaiuClient: NCLOUD_ACCESS_KEY is required"),
    ],
)
def test_operator_reason_never_carries_our_endpoint(exc: Exception) -> None:
    """어떤 분기를 거쳐도 화면 문자열에 내부 경로·약어가 없는지 확인한다."""
    said = operator_reason(exc)
    assert said, "사유가 비면 화면이 '분석 실패 — '로 끝난다"
    assert not _leaks(said), f"내부 조각이 남았다: {said!r}"


def test_vendor_korean_reason_survives() -> None:
    """외부 서비스가 준 한국어 사유와 코드가 남는지 확인한다. 한국어 사유는 운영자에게 가장 쓸모 있는 정보다."""
    exc = RuntimeError(
        f'MAIU POST {_PATH} → 409 {{"code":"10041","message":"버킷 연동이 필요합니다"}}'
    )
    said = operator_reason(exc)
    assert "버킷 연동이 필요합니다" in said
    assert "10041" in said, "코드는 문의할 때 쓴다 — 같이 남긴다"


def test_status_speaks_when_vendor_gives_no_reason() -> None:
    """본문이 비어도 상태 코드로 사유를 만드는지 확인한다. 모든 경우를 '사유 미확인' 하나로 합치지 않는다.

    상태 코드 숫자만 보여 주지 않는다.
    429는 운영자가 할 일(잠시 뒤 다시 요청)이 정해져 있으므로 그 안내를 한다.
    5xx는 이 시스템에서 할 수 있는 조치가 없어서 문의할 때 쓰도록 코드를 함께 남긴다.
    """
    throttled = operator_reason(MaiuTransientError(f"MAIU GET {_PATH} → 429 {{}}", status=429))
    assert throttled != "사유 미확인"
    assert "제한" in throttled, f"429를 요청량 제한이라고 말해야 한다: {throttled!r}"

    server = operator_reason(RuntimeError(f"MAIU GET {_PATH} → 503 "))
    assert "503" in server, f"5xx는 코드를 남긴다: {server!r}"


def test_our_own_exception_does_not_reach_the_screen() -> None:
    """시스템 내부 예외의 원문을 화면 사유에 싣지 않는지 확인한다. 운영자는 내부 예외를 읽고 조치할 수 없다.

    외부 서비스는 사유를 한국어로 주므로 한글 포함 여부로 내부 예외를 구분한다.
    예를 들어 `source_uri`가 비면 `_parse_uri`가 `'NoneType' object has no attribute 'startswith'`로 실패한다.
    이 문자열이 `dispatch_error`에 그대로 저장되면 화면에 파이썬 예외가 표시된다.
    """
    for exc in (
        AttributeError("'NoneType' object has no attribute 'startswith'"),
        KeyError("videoUri"),
        ZeroDivisionError("division by zero"),
    ):
        said = operator_reason(exc)
        assert "NoneType" not in said and "division" not in said
        assert "서버 기록" in said, f"원문이 남은 곳까지 알린다: {said!r}"


def test_reason_carries_one_dash_at_most() -> None:
    """사유 조각에 줄표가 없는지 확인한다. 화면이 `분석 실패 — {조각}`으로 조립하므로 조각에 줄표가 있으면 줄표가 두 개가 된다."""
    samples = [
        AttributeError("boom"),
        RuntimeError(f"MAIU GET {_PATH} → 503 "),
        MaiuTransientError(f"MAIU GET {_PATH} → 429 {{}}", status=429),
        RuntimeError("MAIU: no indexes for asset 1"),
    ]
    for exc in samples:
        assert "—" not in operator_reason(exc), f"줄표가 조각 안에 있다: {exc}"


def test_raw_detail_keeps_everything() -> None:
    """`raw_detail`이 경로·상태·코드를 모두 남기는지 확인한다. 화면에서 뺀 정보가 기록에서도 사라지면 관리자가 원인을 추적할 수 없다."""
    exc = RuntimeError(f'MAIU POST {_PATH} → 409 {{"code":"10097"}}')
    raw = raw_detail(exc)
    assert _PATH in raw
    assert "409" in raw
    assert "10097" in raw


def test_raw_detail_respects_the_column_limit() -> None:
    """`raw_detail`이 길이 제한을 지키는지 확인한다. 결과가 `error_msg`·payload에 저장된다."""
    assert len(raw_detail(RuntimeError("가" * 5000), 400)) == 400


# 준비 과정의 다른 단계
#
# `stage_runs`가 공통 경로에서 부르므로 모든 단계의 사유가 이 번역을 거친다.

def test_korean_reasons_pass_through_untouched() -> None:
    """이미 운영자용 문장으로 쓴 사유가 한 글자도 바뀌지 않는지 확인한다.

    공통 경로에서 번역하려면 이 조건이 지켜져야 한다. 지켜지지 않으면 정상 문구가 화면에서 바뀐다.
    실제 코드에 있는 문자열을 그대로 쓴다.
    """
    for said in (
        "원본 URI 없음",
        "워커가 응답하지 않습니다. 리스가 만료돼 자동 회수했습니다",
        "새 시도가 시작되어 이 시도는 버려졌습니다",
        "재생본 입력 없음",
        "비식별화 입력 없음",
        "장면 요약을 요청하지 못했습니다",
        "장면 요약을 만들지 못했습니다",
        "결과 파일을 저장소에 올리지 못했습니다",
    ):
        assert operator_text(said) == said


def test_ffprobe_failures_say_why_not_how() -> None:
    """규격 검사 실패 사유가 원인을 알려 주고 도구 이름·종료 코드·URL은 빼는지 확인한다.

    URL을 빼지 않으면 준비 타임라인 시도 행에 presigned URL이 쿼리까지 그대로 표시된다.
    """
    url = "https://kr.object.ncloudstorage.com/example-bucket/seed/news9-demo.mp4?…"
    cases = {
        f"ffprobe failed (rc=1): {url} Server returned 404 Not Found": "찾지 못했",
        f"ffprobe failed (rc=1): {url} Server returned 403 Forbidden": "접근하지 못했",
        "ffprobe failed (rc=1): Invalid data found when processing input": "손상",
        "ffprobe timeout after 30s": "시간",
        "ffprobe not found — worker-io 이미지에서 실행해야 한다": "도구",
        "no video stream found": "영상 트랙",
    }
    for raw, expect in cases.items():
        said = operator_text(raw)
        assert expect in said, f"{raw!r} -> {said!r}"
        assert "ffprobe" not in said and "rc=" not in said and "http" not in said


def test_probe_status_is_read_from_the_sentence_not_loose_digits() -> None:
    """상태 코드를 숫자만으로 찾지 않고 문장에서 읽는지 확인한다. stderr에 URL이 섞여 있기 때문이다.

    버킷 이름이나 키에 `404`가 들어 있으면 숫자만으로 찾는 구현은 오판한다.
    `_redact`가 쿼리를 지워서 지금은 오판하지 않지만 그 결과에 기대지 않는다.
    그래서 ffmpeg이 쓰는 문장 형식(`Server returned NNN`)으로 읽는지 고정한다.
    """
    trap = "https://kr.object.ncloudstorage.com/bucket-404-test/2026-403/x.mp4?…"
    said = operator_text(f"ffprobe failed (rc=1): {trap} Server returned 403 Forbidden")
    assert "접근하지 못했" in said, f"URL의 404에 끌려갔다: {said!r}"

    said = operator_text(f"ffprobe failed (rc=1): {trap} Server returned 503 Service Unavailable")
    assert "응답하지 않" in said, f"5xx를 못 읽었다: {said!r}"


def test_none_and_blank_stay_none() -> None:
    """빈 사유가 빈 문자열이 아니라 None으로 남는지 확인한다.

    실패했는데 사유가 없는 경우와 사유가 있는 경우를 구분한다.
    """
    assert operator_text(None) is None
    assert operator_text("") is None
    assert operator_text("   ") is None


# 변환 산출 검사: 코드를 운영자 문구로 바꾼다


def test_output_check_reasons_say_how_far_and_the_rule() -> None:
    """변환 산출 검사 사유가 얼마나 어긋났는지와 기준을 함께 알려 주는지 확인한다.

    차이만 알려 주면 `0.6초 짧습니다`가 왜 실패인지 알 수 없다.
    1분 미만을 소수 한 자리로 자르면 `0.54초`가 `0.5초`로 보여 기준과 같아진다.
    그러면 기준을 넘은 값이 기준 안으로 읽힌다.
    """
    from api_gateway.services.error_text import output_check_text

    said = output_check_text("output_duration_mismatch", output="분석본",
                             delta_seconds=-0.54, tolerance_seconds=0.5)
    assert said == "분석본 길이가 원본보다 0.54초 짧습니다. 허용 오차는 0.5초입니다"
    longer = output_check_text("output_duration_mismatch", output="재생본",
                               delta_seconds=734.2, tolerance_seconds=0.5)
    assert longer.startswith("재생본 길이가 원본보다 12분 14초 깁니다")
    assert output_check_text("output_unreadable", output="분석본") == "분석본 파일을 읽지 못했습니다"
    # 모르는 코드나 빠진 수치는 코드를 화면에 싣지 않고 일반 문구로 바꾼다.
    for vague in (output_check_text("output_weird", output="분석본"),
                  output_check_text("output_duration_mismatch", output="분석본")):
        assert vague == "분석본 검사를 통과하지 못했습니다"


def test_output_check_reasons_pass_the_stage_gate_untouched() -> None:
    """산출 검사 사유가 준비 타임라인의 공통 경로(`operator_text`)를 지나도 그대로인지 확인한다.

    산출 검사 사유는 이미 한국어 문장이라 번역 없이 그대로 지나가야 한다.
    """
    from api_gateway.services.error_text import output_check_text

    said = output_check_text("output_duration_mismatch", output="분석본",
                             delta_seconds=-300.0, tolerance_seconds=0.5)
    assert operator_text(said) == said


# 분석 실패 줄: 저장된 옛 값도 화면 문구로 바꾼다
#
# 쓸 때만 번역하면 이미 저장된 행이 그대로 나간다. 아래 값은 이전 형식으로 저장된 행의 예다.


def test_legacy_dispatch_error_hides_python_exception() -> None:
    """옛 요청 실패 사유의 파이썬 예외와 외부 서비스 이름이 화면에 나가지 않는지 확인한다."""
    from api_gateway.services.error_text import DISPATCH_HEAD, analysis_error_text

    said = analysis_error_text(
        "MI 요청 실패 — 'NoneType' object has no attribute 'startswith'", head=DISPATCH_HEAD,
    )
    assert said is not None
    assert "NoneType" not in said and "startswith" not in said, said
    assert not said.startswith("MI"), f"벤더 이름이 앞말로 남았다: {said!r}"
    assert said.startswith(DISPATCH_HEAD), said


def test_raw_vendor_path_becomes_summary_with_status() -> None:
    """경로가 붙은 원문이 상태 코드와 외부 서비스 사유를 살린 한 줄로 바뀌는지 확인한다."""
    from api_gateway.services.error_text import analysis_error_text

    raw = (f"MAIU POST {_PATH}0/analyze → 400 "
           '{"code":"10001","message":"유효하지 않은 요청입니다."}')
    said = analysis_error_text(raw)
    assert said is not None
    assert not _leaks(said), f"내부 조각이 남았다: {said!r}"
    assert "(400)" in said, f"400이라는 사실이 빠졌다: {said!r}"
    assert "유효하지 않은 요청입니다" in said and "10001" in said, said
    assert said.startswith("분석 서비스"), f"누가 거절했는지가 빠졌다: {said!r}"
    assert ". (" not in said, f"마침표 뒤에 괄호가 붙었다: {said!r}"


def test_transient_marker_and_old_vendor_word_are_rewritten() -> None:
    """`[일시]` 표지와 옛 용어(`분석 서버`)가 화면 문구로 바뀌는지 확인한다."""
    from api_gateway.services.error_text import analysis_error_text

    assert analysis_error_text("[일시] 분석 서버 오류 (503)") == "분석 서비스 오류 (503)"


def test_dispatch_prefix_forms_all_read_the_same() -> None:
    """요청 실패 앞말의 옛 형식 세 가지와 지금 형식이 같은 줄로 읽히는지 확인한다."""
    from api_gateway.services.error_text import analysis_error_text

    expected = "분석 요청을 보내지 못했습니다: 유효하지 않은 요청입니다 (코드 10001)"
    for stored in (
        "MI 거절 — 유효하지 않은 요청입니다. (code 10001)",
        "분석 요청 거절 — 유효하지 않은 요청입니다 (코드 10001)",
        "분석 요청 거절 · 유효하지 않은 요청입니다 (코드 10001)",
        expected,
    ):
        assert analysis_error_text(stored) == expected, stored


def test_analysis_error_text_is_idempotent() -> None:
    """번역을 두 번 거쳐도 결과가 같은지 확인한다. 읽을 때마다 번역하므로 멱등이어야 한다."""
    from api_gateway.services.error_text import DISPATCH_HEAD, analysis_error_text

    for stored in (
        f"MAIU GET {_PATH} → 503 upstream unavailable",
        "MI 요청 실패 — 'NoneType' object has no attribute 'startswith'",
        "원본 파일이 없습니다. 저장소에서 삭제됐거나 경로가 바뀌었습니다",
        "[일시] 분석 요청량 제한에 걸렸습니다",
    ):
        once = analysis_error_text(stored, head=DISPATCH_HEAD)
        assert analysis_error_text(once, head=DISPATCH_HEAD) == once, stored


def test_analysis_error_text_keeps_blank_as_none() -> None:
    """빈 사유가 None으로 남는지 확인한다. 사유가 없는 경우와 있는 경우를 구분한다."""
    from api_gateway.services.error_text import analysis_error_text

    assert analysis_error_text(None) is None
    assert analysis_error_text("  ") is None


def test_korean_head_with_raw_tail_keeps_only_the_head() -> None:
    """한국어 앞말 뒤에 원문이 붙은 값에서 앞말만 남기는지 확인한다. 한글이 있다는 이유로 값 전체를 통과시키지 않는다."""
    assert operator_text("산출 승격 실패: ClientError") == "산출 승격 실패"
    said = operator_text("요청 실패 — 'NoneType' object has no attribute 'startswith'")
    assert said is not None and "NoneType" not in said, said


def test_dispatch_error_text_from_exception() -> None:
    """새로 저장하는 요청 실패 사유도 읽을 때와 같은 형식인지 확인한다."""
    from api_gateway.services.error_text import dispatch_error_text

    said = dispatch_error_text(RuntimeError(
        f'MAIU POST {_PATH} → 400 {{"code":"10041","message":"버킷 연동이 유효하지 않습니다"}}'
    ))
    assert said.startswith("분석 서비스가 요청을 거절했습니다 (400)"), said
    assert "버킷 연동이 유효하지 않습니다" in said and "10041" in said
    assert not _leaks(said)

    ours = dispatch_error_text(AttributeError("'NoneType' object has no attribute 'startswith'"))
    assert ours == "분석 요청을 보내지 못했습니다: 원인을 확인하지 못했습니다", ours


def test_readable_video_extra_rewrites_only_dispatch_error() -> None:
    """영상 extra에서 요청 실패 사유만 바꾸고 원본 dict는 그대로 두는지 확인한다."""
    from api_gateway.services.error_text import readable_video_extra

    extra = {
        "dispatch_error": "MI 요청 실패 — 'NoneType' object has no attribute 'startswith'",
        "key": "seed/a.mp4",
    }
    out = readable_video_extra(extra)
    assert out is not None and out is not extra
    assert "NoneType" not in out["dispatch_error"]
    assert out["key"] == "seed/a.mp4"
    assert extra["dispatch_error"].startswith("MI 요청 실패"), "원본을 바꾸면 안 된다"
    assert readable_video_extra(None) is None
    assert readable_video_extra({"key": "x"}) == {"key": "x"}


def test_upload_failure_drops_vendor_status_word() -> None:
    """업로드 실패 사유에서 외부 서비스 사유만 살리고 영문 상태 값은 화면에 내지 않는지 확인한다."""
    from api_gateway.services.error_text import analysis_error_text

    said = operator_text("업로드 실패(FAILED): 지원하지 않는 형식입니다")
    assert said == "분석 서비스가 영상을 받지 못했습니다: 지원하지 않는 형식입니다", said
    assert operator_text("업로드 실패(ERROR): 사유 미제공") == "분석 서비스가 영상을 받지 못했습니다"
    # 이미 저장된 옛 행도 읽을 때 같은 문구로 바뀐다.
    assert analysis_error_text("업로드 실패(FAILED): 지원하지 않는 형식입니다") == said


def test_old_summary_skipped_text_is_read_in_plain_words() -> None:
    """옛 저장 문구들이 읽을 때 지금의 장면 요약 사유로 바뀌는지 확인한다.

    장면 요약은 분석이 끝난 뒤의 단계라서 `분석하지 못했습니다`를 앞에 붙이지 않는다.
    붙이면 끝난 분석이 실패한 것으로 읽힌다. MI의 상태 값(`SUMMARIZE_FAIL`)도 화면에 싣지 않는다.
    """
    from api_gateway.services.error_text import (
        SUMMARY_FAILED_TEXT,
        SUMMARY_SKIPPED_TEXT,
        analysis_error_text,
    )

    cases = (
        ("요약 요청 누락(best-effort 완료)", SUMMARY_SKIPPED_TEXT),
        ("장면 요약을 요청하지 못해 요약 없이 완료했습니다", SUMMARY_SKIPPED_TEXT),
        (SUMMARY_SKIPPED_TEXT, SUMMARY_SKIPPED_TEXT),
        ("요약에 실패했습니다 (SUMMARIZE_FAIL). 클립은 유지됩니다", SUMMARY_FAILED_TEXT),
        ("요약에 실패했습니다 (FAILED). 클립은 유지됩니다", SUMMARY_FAILED_TEXT),
        (SUMMARY_FAILED_TEXT, SUMMARY_FAILED_TEXT),
    )
    for stored, said in cases:
        assert operator_text(stored) == said
        assert analysis_error_text(stored) == said
        assert analysis_error_text(analysis_error_text(stored)) == said
