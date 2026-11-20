"""실패 원문을 운영자가 읽을 사유 조각으로 바꾸는 모듈. 원문은 진단용으로 따로 남긴다.

준비 여정의 모든 단계(규격 검사·변환·비식별화·분석·장면 분할·재생본)가 같은 화면 두 곳에
실패 사유를 싣는다. 준비 단계 이슈 레인(`분석 실패 — …`)과 준비 타임라인의 시도 줄이다.
판정을 도메인마다 두면 같은 실패가 화면에서 다르게 표시되므로 `audit`·`authz`처럼 범도메인
모듈로 둔다.

`str(exc)` 원문은 다음과 같은 꼴이다.

    MAIU POST /api/v1/workspaces/example-workspace/projects/1/video-assets/… → 409 {…}
    ffprobe failed (rc=1): https://kr.object.ncloudstorage.com/…/news9-demo.mp4?… 404
    'NoneType' object has no attribute 'startswith'
    산출 승격 실패: ClientError

원문은 내부 경로·도구 이름·파이썬 예외 타입으로 이루어져 운영자가 대응할 수 없다.
화면이 80~120자에서 자르므로 무엇이 잘못됐는지도 잘려서 보이지 않는다.

운영자용 사유인지는 한글이 있는지로 구분한다. 이 앱의 문구와 MI가 주는 사유는 한국어다
(`{"code":"10041", "message":"저장소 연결이 …"}`). 내부 문자열만 영문이다. 그래서 한국어 사유는
그대로 통과시키고 영문 원문은 화면에서 뺀다.

한글이 있어도 개발자 원문이 섞여 있으면 한국어 앞말만 남기거나 원문을 뺀다. 저장된 옛 행에는
한국어 앞말 뒤에 원문이 붙어 있다(`MI 요청 실패 — 'NoneType' …`).

분석 실패 사유는 `analysis_error_text`가 화면에 그대로 표시할 한 줄로 만든다.
MI 잡의 `error_msg`와 영상의 `extra["dispatch_error"]`는 읽을 때도 `analysis_error_text`를 거친다.
저장된 값에 번역 전 원문과 옛 앞말이 남아 있어서 쓸 때만 번역하면 옛 행이 그대로 화면에 나간다.

원문은 감사 이벤트와 `payload["last_error_raw"]`에 남겨 관리자가 추적할 수 있게 한다.
화면에서 빼도 기록에는 남는다.

`operator_reason`·`operator_text`가 돌려주는 값은 사유 조각이다. 호출하는 쪽이
`분석 실패 — {조각}`처럼 앞말을 붙인다(`blockOf`의 `변환 실패 — {err}`와 같은 구조).
조각에 다시 `—`를 넣으면 화면에 줄표가 둘 생긴다.
"""
from __future__ import annotations

import json
import re

__all__ = [
    "ANALYSIS_HEAD", "ANALYSIS_TRANSIENT_LABEL", "DISPATCH_DEFERRED_KEY", "DISPATCH_HEAD",
    "NO_INPUT_TEXT", "OUTPUT_CHECK_TEXT",
    "analysis_error_text", "analysis_transient_kind", "dispatch_deferred_view",
    "dispatch_error_text", "operator_reason", "operator_text",
    "output_check_text", "raw_detail", "raw_or_none", "readable_video_extra",
]

# `MAIU {METHOD} {path} → ` 접두어 패턴. 접두어만 지우고 뒤의 상태·본문은 남긴다.
_ENDPOINT = re.compile(r"^MAIU\s+[A-Z]+\s+\S+\s*→\s*")
# 다른 접두어 형태(`MAIU: …`, `MAIU register_asset: …`)의 패턴. 앞말만 지운다.
_LABEL = re.compile(r"^MAIU(?:\s+\w+)?\s*:\s*")
_STATUS = re.compile(r"^(\d{3})\b")
# 경로 뒤의 상태 코드(`… → 400 {…}`) 패턴. 본문 앞부분에서만 찾는다.
_ARROW_STATUS = re.compile(r"→\s*(\d{3})\b")
_JSON = re.compile(r"\{.*\}", re.S)
_HANGUL = re.compile(r"[가-힣]")
# 한글 문장에 섞여 들어온 개발자 원문의 표지. 예외 이름·파이썬 오류 문장·경로·주소.
_DEV_TEXT = re.compile(
    r"NoneType|object has no attribute|Traceback|/api/|https?://"
    r"|\b[A-Z]\w*(?:Error|Exception)\b"
)
# 한국어 앞말과 원문 꼬리를 가르는 구분자.
_SEP = re.compile(r"\s*(?:—|·|:)\s*")
# 분석 서비스가 영상 업로드 실패를 알린 사유의 형태(`reconciler.bulk_poll_registrations`).
# 괄호 안은 벤더의 영문 상태 값이라 화면에 내지 않는다. 원문은 `last_error_raw`에 남는다.
_UPLOAD_FAILED = re.compile(r"^업로드 실패\(\w*\)\s*:\s*(.*)$", re.S)
# 벤더가 사유를 주지 않았을 때 사유 대신 들어 있는 문구.
_NO_VENDOR_REASON = "사유 미제공"

#: 이 앱의 예외처럼 원문에서 원인을 알아낼 수 없을 때의 사유 조각.
_UNREADABLE = "원인을 읽지 못했습니다(원문은 서버 기록에 남아 있습니다)"

# 벤더 응답 본문의 사유·코드 키 이름. 응답마다 달라서 확인된 이름을 모두 받는다.
_MSG_KEYS = ("message", "errorMessage", "msg", "detail")
_CODE_KEYS = ("code", "errorCode", "returnCode")

#: 장면 요약 사유의 머리말. 요약은 분석이 끝난 뒤의 단계라서 이 말로 시작하는 사유에는
#: 분석 실패 머리말(`ANALYSIS_HEAD`)을 붙이지 않는다. 붙이면 끝난 분석이 실패한 것으로 읽힌다.
SUMMARY_HEAD = "장면 요약"
#: 장면 요약 요청 번호가 없어 요약을 받지 못한 잡의 사유.
SUMMARY_SKIPPED_TEXT = "장면 요약을 요청하지 못했습니다"
#: MI가 장면 요약 실패를 알린 잡의 사유.
#: MI의 상태 값(`SUMMARIZE_FAIL`)은 운영자가 대응할 정보가 아니라서 화면 문구에 싣지 않는다.
#: 상태 값은 감사 이벤트(`maiu.stage.summarize_failed`)에만 남긴다.
SUMMARY_FAILED_TEXT = "장면 요약을 만들지 못했습니다"
# MI가 요약 실패를 알린 사유의 옛 저장 형태. 괄호 안은 MI의 상태 값이다.
_LEGACY_SUMMARY_FAIL = re.compile(r"^요약에 실패했습니다 \([^)]*\)\. 클립은 유지됩니다$")


# 이 앱이 직접 던지는 예외 문자열과 화면 사유의 대응표.
# 예외 문구는 로그와 `last_error_raw`에서 진단에 쓰이므로 개발자용 영문 그대로 둔다.
# 화면에 나갈 사유만 여기서 정한다.
_INTERNAL: tuple[tuple[str, str], ...] = (
    # `SUMMARY_SKIPPED_TEXT`의 옛 문구들. 저장된 행에 남아 있어 읽을 때 바꾼다.
    # 옛 문구는 요약 없이 완료했다고 적지만 그 영상은 요약을 다시 받을 때까지 막힘으로 분류된다.
    ("요약 요청 누락(best-effort 완료)", SUMMARY_SKIPPED_TEXT),
    ("장면 요약을 요청하지 못해 요약 없이 완료했습니다", SUMMARY_SKIPPED_TEXT),
    # 분석(MI): 벤더가 200을 돌려줬지만 응답에 필요한 값이 없는 경우.
    ("no assetId", "분석 등록 응답에 자산 번호가 없습니다"),
    ("no videoId", "분석 등록 응답에 자산 번호가 없습니다"),
    ("no asset_id", "분석 등록 번호가 없습니다"),
    ("no analysis_id", "분석 요청 번호가 없습니다"),
    ("no indexes for asset", "분석 결과가 아직 없습니다"),
    ("no sceneSummaryId", "장면 요약 응답에 요청 번호가 없습니다"),
    ("no id in", "변환 응답에 요청 번호가 없습니다"),
    ("cannot parse uri", "원본 경로를 읽지 못했습니다"),
    ("is required", "분석 서비스 설정이 비어 있습니다"),
    # 규격 검사(ffprobe): 운영자가 대응할 수 없는 도구 이름과 종료 코드를 사유 문장으로 바꾼다.
    ("ffprobe timeout", "규격 검사가 시간 안에 끝나지 않았습니다"),
    ("ffprobe not found", "규격 검사 도구를 찾지 못했습니다"),
    ("ffprobe output is not valid JSON", "규격 검사 결과를 읽지 못했습니다"),
    ("no video stream", "영상 트랙이 없습니다"),
)

# 규격 검사 실패 stderr의 원인 문구와 화면 사유. 예외 문자열에는 ffprobe stderr의 끝부분이 담긴다.
# URL·`rc=` 같은 내부 조각은 빼고 원인만 화면 사유로 바꾼다.
_PROBE_CAUSE: tuple[tuple[str, str], ...] = (
    ("Invalid data found", "원본이 손상됐거나 영상 파일이 아닙니다"),
    ("moov atom not found", "원본이 손상됐습니다(재생 정보 없음)"),
    ("Connection refused", "원본 저장소에 연결하지 못했습니다"),
    ("Connection timed out", "원본 저장소가 응답하지 않습니다"),
)

# 상태 코드는 ffmpeg이 쓰는 문장 형태(`Server returned`·`HTTP error`)에서만 읽는다.
# stderr에는 URL이 섞여 있어 숫자만 찾으면 버킷 이름이나 키의 `404`를 상태 코드로 오판한다.
# `_redact`는 쿼리만 지우므로 이 오판을 막지 못한다.
_HTTP_STATUS = re.compile(r"(?:Server returned|HTTP error)\s+(\d{3})")
_PROBE_STATUS: dict[int, str] = {
    404: "원본을 찾지 못했습니다(파일이 없거나 경로가 바뀌었습니다)",
    403: "원본에 접근하지 못했습니다",
    401: "원본에 접근할 자격이 없습니다",
}


def _body(text: str) -> dict | None:
    """예외 문자열에 섞인 JSON 본문을 꺼낸다. 꺼내지 못하면 None을 돌려준다."""
    m = _JSON.search(text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _pick(body: dict, keys: tuple[str, ...]) -> str | None:
    """본문에서 값이 있는 첫 번째 키의 값을 돌려준다. 한 단계 중첩(`error`·`result`)까지 찾는다."""
    for scope in (body, body.get("error"), body.get("result")):
        if not isinstance(scope, dict):
            continue
        for k in keys:
            v = scope.get(k)
            if v not in (None, ""):
                return str(v)
    return None


def _status_head(status: int) -> str:
    """분석 서비스가 돌려준 상태 코드를 화면 문구로 바꾼다.

    본문에 벤더 사유가 있을 때와 없을 때 같은 문구를 쓰도록 한 곳에 둔다.
    화면에는 벤더 이름 대신 `분석 서비스`라고 쓴다.
    """
    if status == 429:
        return "분석 서비스 요청량 제한을 초과했습니다"
    if status >= 500:
        return f"분석 서비스 오류 ({status})"
    if status == 404:
        return "분석 서비스가 대상을 찾지 못했습니다 (404)"
    if status in (401, 403):
        return f"분석 서비스 인증이 거절됐습니다 ({status})"
    return f"분석 서비스가 요청을 거절했습니다 ({status})"


def operator_reason(exc: BaseException) -> str:
    """예외를 화면에 실을 사유 조각으로 바꾼다. 결과에는 내부 경로를 담지 않는다.

    우선순위는 벤더 응답 본문의 사유, 알려진 실패, 상태 코드, 한글이 있는 원문, 기본 문구 순이다.

    마지막 단계까지 내려오는 예외는 대개 이 앱의 예외
    (`'NoneType' object has no attribute 'startswith'` 같은 것)다. 운영자는 이 원문을 읽어도
    대응할 수 없다. 벤더는 사유를 한국어로 주므로 한글이 없는 원문은 이 앱의 예외로 보고
    화면에서 뺀다. 원문은 `raw_detail`이 감사 기록(`domain_events`)과 `last_error_raw`에 남긴다.
    감사 기록을 보는 화면이 없으므로 사유는 "알 수 없음" 대신 원문이 서버 기록에 있다는
    안내(`_UNREADABLE`)로 쓴다.

    Args:
        exc: `naver_client`가 던진 예외나 같은 형태의 예외.

    Returns:
        `분석 실패 — {반환값}` 형태로 이어 붙일 조각. 늘 비어 있지 않은 문자열이다.
    """
    text = str(exc).strip()

    body = _body(text)
    if body:
        msg = _pick(body, _MSG_KEYS)
        if msg:
            # 벤더 사유 끝의 마침표를 떼고 코드를 괄호로 붙인다.
            # 마침표 뒤에 코드가 붙는 형태(`…입니다. (코드 1)`)를 막는다.
            said = msg.strip().rstrip(".")
            code = _pick(body, _CODE_KEYS)
            said = f"{said} (코드 {code})" if code else said
            # 상태 코드가 있으면 `_status_head`의 문구를 앞에 붙인다.
            # 벤더 사유만 남기면 상태 코드와 주어(분석 서비스)가 빠진다.
            m = _ARROW_STATUS.search(text[: text.find("{")] if "{" in text else text)
            return f"{_status_head(int(m.group(1)))}: {said}" if m else said

    if "ffprobe failed" in text:
        m = _HTTP_STATUS.search(text)
        if m:
            code = int(m.group(1))
            return _PROBE_STATUS.get(code) or (
                "원본 저장소가 응답하지 않습니다" if code >= 500
                else f"원본을 읽지 못했습니다 ({code})"
            )
        for needle, said in _PROBE_CAUSE:
            if needle in text:
                return said
        return "원본을 읽지 못했습니다"

    m = _LEGACY_SUMMARY_FAIL.match(text)
    if m:
        return SUMMARY_FAILED_TEXT

    for needle, said in _INTERNAL:
        if needle in text:
            return said

    stripped = _ENDPOINT.sub("", text)
    stripped = _LABEL.sub("", stripped).strip()

    if "transport error" in text:
        return "분석 서비스에 연결하지 못했습니다"

    m = _STATUS.match(stripped)
    if m:
        return _status_head(int(m.group(1)))

    m = _UPLOAD_FAILED.match(stripped)
    if m:
        # 벤더가 준 한국어 사유는 남기고 영문 상태 값만 뺀다.
        said = m.group(1).strip()
        head = "분석 서비스가 영상을 받지 못했습니다"
        return f"{head}: {said}"[:200] if said and said != _NO_VENDOR_REASON else head

    if stripped and _HANGUL.search(stripped):
        if not _DEV_TEXT.search(stripped):
            return stripped[:200]
        # 한국어 앞말 뒤에 원문이 붙은 옛 형태는 앞말만 남긴다.
        head = _SEP.split(stripped, maxsplit=1)[0].strip()
        if head and _HANGUL.search(head) and not _DEV_TEXT.search(head):
            return head[:200]
    return _UNREADABLE


def operator_text(value: object) -> str | None:
    """예외나 문자열을 받아 화면에 실을 사유로 바꾼다. 한국어 사유는 바꾸지 않는다.

    준비 여정의 실패 사유를 모으는 `media.stage_runs`가 부른다. 단계마다 따로 번역하면
    단계를 더할 때 번역을 빠뜨리기 쉬우므로 한 곳에서 번역한다.

    이미 운영자용으로 쓴 사유(`원본 URI 없음`·`리스 만료 — 워커 무응답, 자동 회수됨` 등)는
    그대로 두고 200자를 넘으면 자른다. 한글이 있으면 이 앱이 쓴 문장으로 보고 통과시킨다.
    영문 원문만 사유 문구로 바뀐다.

    Args:
        value: 예외, 문자열, 또는 None·빈 값.

    Returns:
        사유 조각. 입력이 비었으면 `None`. 호출부가 사유 없는 실패와 사유 있는 실패를
        구분하므로 빈 문자열 대신 `None`을 돌려준다.
    """
    if value is None:
        return None
    if isinstance(value, BaseException):
        return operator_reason(value)
    text = str(value).strip()
    if not text:
        return None
    return operator_reason(RuntimeError(text))


#: 변환 산출 검사의 위반 코드별 화면 사유. 문장은 이 사전에서만 정한다.
#: 키는 `reborn_transcode.output_check`의 상수와 같다. 집계는 `media_stage_runs.detail.cause`에
#: 남은 코드로 하므로 문구를 다듬어도 집계는 바뀌지 않는다.
#: 산출물 이름 뒤에는 조사 대신 명사를 둔다(`{output} 파일을`·`{output} 길이가`).
#: 그래서 산출물 이름이 늘어도 받침 때문에 조사가 틀리지 않는다.
OUTPUT_CHECK_TEXT: dict[str, str] = {
    "output_unreadable": "{output} 파일을 읽지 못했습니다",
    "output_check_timeout": "{output} 검사가 시간 안에 끝나지 않았습니다",
    "output_duration_mismatch": (
        "{output} 길이가 원본보다 {delta} {direction}. 허용 오차는 {tolerance}입니다"),
}


def _span(seconds: float) -> str:
    """초를 화면 표기로 바꾼다.

    1분 미만은 소수 둘째 자리까지, 1시간 미만은 분·초, 그 이상은 시간·분으로 쓴다.
    1분 미만을 소수 한 자리로 자르면 `0.54초`가 `0.5초`가 되어 기준(0.5초)과 같아 보인다.
    그러면 기준을 넘어 실패한 검사를 기준 안으로 잘못 표시한다.
    """
    s = abs(seconds)
    if s < 60:
        return f"{s:.2f}".rstrip("0").rstrip(".") + "초"
    m, sec = divmod(round(s), 60)
    if m < 60:
        return f"{m}분 {sec}초" if sec else f"{m}분"
    h, m = divmod(m, 60)
    return f"{h}시간 {m}분" if m else f"{h}시간"


def output_check_text(
    cause: str, *, output: str,
    delta_seconds: float | None = None, tolerance_seconds: float | None = None,
) -> str:
    """변환 산출 검사의 위반을 화면 사유로 바꾼다. 준비 타임라인의 `사유 보기`에 실린다.

    어긋난 정도와 기준을 함께 표시한다. 차이만 표시하면 `0.6초 짧습니다`가 왜 실패인지
    읽는 사람이 알 수 없다.

    Args:
        cause: 위반 코드(`output_unreadable`·`output_check_timeout`·`output_duration_mismatch`).
        output: 산출물 이름. 화면에서 쓰는 이름이다(`분석본`·`재생본`).
        delta_seconds: 산출물 길이에서 원본 길이를 뺀 값(초). 음수면 산출물이 짧다.
        tolerance_seconds: 허용 오차(초).

    Returns:
        사유 조각. 사전에 없는 코드이거나 수가 비었으면 `{output} 검사를 통과하지 못했습니다`를
        돌려준다. 위반 코드는 화면에 싣지 않는다.
    """
    template = OUTPUT_CHECK_TEXT.get(cause)
    if template is None or (
        cause == "output_duration_mismatch"
        and (delta_seconds is None or tolerance_seconds is None)
    ):
        return f"{output} 검사를 통과하지 못했습니다"
    if cause != "output_duration_mismatch":
        return template.format(output=output)
    return template.format(
        output=output, delta=_span(delta_seconds),
        direction="짧습니다" if delta_seconds < 0 else "깁니다",
        tolerance=_span(tolerance_seconds),
    )


def raw_detail(exc: BaseException, limit: int = 1000) -> str:
    """진단용 원문을 돌려준다. 경로·상태·본문을 그대로 남긴다(최대 `limit`자).

    화면에서 뺀 정보를 기록에서도 지우면 관리자가 원인을 추적할 수 없다. 이 값은
    `job.payload["last_error_raw"]`와 감사 이벤트에만 쓰인다. 두 곳 모두 운영자 화면에 표시되지
    않는다.
    """
    return str(exc)[:limit]


def raw_or_none(value: object, limit: int = 400) -> str | None:
    """감사 payload에 실을 원문을 돌려준다.

    `raw_detail`과 달리 예외가 아닌 값도 받는다. 값이 비었으면 `None`을 돌려준다.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


#: 분석 잡 실패 줄의 머리말. 사유에 주어가 없을 때 앞에 붙인다.
ANALYSIS_HEAD = "분석하지 못했습니다"
#: 분석 요청(dispatch) 실패 줄의 머리말.
DISPATCH_HEAD = "분석 요청을 보내지 못했습니다"
#: 분석에 보낼 파일 경로가 없는 영상의 요청 실패 줄. 원본과 분석본 모두 경로가 없는 경우다.
NO_INPUT_TEXT = f"{DISPATCH_HEAD}: 영상 파일 경로가 등록되지 않았습니다"
# 요청 실패 앞말 뒤에 붙은 옛 원문 가운데 원인이 확실한 원문과 그 화면 사유.
# 요청 경로에서 이 예외는 경로가 없는 영상의 URI를 나누는 한 곳에서만 난다.
# 그래서 앞말 없이 들어온 같은 예외에는 이 사유를 붙이지 않는다.
_LEGACY_DISPATCH: tuple[tuple[str, str], ...] = (
    ("'NoneType' object has no attribute 'startswith'", NO_INPUT_TEXT),
)

# 저장된 분석 실패 문자열 앞의 재시도 표지(`[일시]`). 재시도 정책이 붙인 내부 표지라 화면에서 뗀다.
_TRANSIENT_MARK = re.compile(r"^\[일시\]\s*")
# 저장된 요청 실패 사유의 앞말. 옛 형태(`MI 요청 실패`·`MI 거절`·`분석 요청 거절`)와 지금 형태를
# 모두 받는다. 벤더 이름이 든 앞말은 화면에 내지 않는다.
_DISPATCH_PREFIX = re.compile(
    r"^(?:MI 요청 실패|MI 거절|분석 요청 거절|분석 요청을 보내지 못했습니다)\s*(?:—|·|:)\s*"
)
_CODE_EN = re.compile(r"\(code (\w+)\)")
# 벤더 사유의 마침표 뒤에 코드가 붙은 옛 형태(`…입니다. (코드 1)`).
_CODE_AFTER_DOT = re.compile(r"\.\s*\((코드 [^)]+)\)")


def analysis_error_text(value: object, *, head: str = ANALYSIS_HEAD) -> str | None:
    """분석 실패 사유를 화면에 그대로 표시할 한 줄로 만든다.

    MI 잡의 `error_msg`와 영상의 `extra["dispatch_error"]`를 읽을 때 부른다.
    저장된 값에는 번역 전 원문(`MAIU POST … → 400 {…}`)과 옛 앞말(`MI 요청 실패 —`)이 남아
    있으므로 쓸 때와 읽을 때 모두 번역한다.
    결과에는 늘 주어가 있다. 사유가 `분석`으로 시작하지 않으면 `head`를 앞에 붙인다.
    결과를 다시 넣어도 같은 결과가 나온다(멱등).

    Args:
        value: 저장된 사유. 문자열·예외·None을 받는다.
        head: 머리말. 요청 실패이면 `DISPATCH_HEAD`를 넘긴다.
            값에 옛 요청 실패 앞말이 있으면 `head` 대신 `DISPATCH_HEAD`를 쓴다.

    Returns:
        화면에 실을 한 줄. 입력이 비었으면 `None`.
    """
    if value is None:
        return None
    text = _TRANSIENT_MARK.sub("", str(value).strip()).strip()
    if not text:
        return None
    m = _DISPATCH_PREFIX.match(text)
    if m:
        head = DISPATCH_HEAD
        text = text[m.end():].strip()
        if not text:
            return head
        for needle, said in _LEGACY_DISPATCH:
            if needle in text:
                return said
    text = _CODE_AFTER_DOT.sub(r" (\1)", _CODE_EN.sub(r"(코드 \1)", text))
    said = (operator_text(text) or "").replace("분석 서버", "분석 서비스")
    # 장면 요약 사유는 분석이 끝난 뒤의 실패라서 머리말을 붙이지 않는다.
    # `분석하지 못했습니다`를 붙이면 끝난 분석이 실패한 것으로 읽힌다.
    if said.startswith(SUMMARY_HEAD):
        return said
    # 요청 실패의 원문은 앱 로그에만 남아 `_UNREADABLE`의 감사 로그 안내가 맞지 않는다.
    # 요청 실패와 MI 잡 실패를 같은 문구로 쓰려고 두 경우 모두 원인을 확인하지 못했다고만 표시한다.
    if not said or said == _UNREADABLE:
        return f"{head}: 원인을 확인하지 못했습니다"
    if said.startswith("분석"):
        return said
    return f"{head}: {said}"


def dispatch_error_text(exc: BaseException) -> str:
    """분석 요청 예외를 `extra["dispatch_error"]`에 저장할 한 줄로 만든다."""
    return analysis_error_text(operator_reason(exc), head=DISPATCH_HEAD) or DISPATCH_HEAD


def readable_video_extra(extra: dict | None) -> dict | None:
    """영상 `extra`의 분석 요청 실패 사유를 화면 말로 바꾼 사본을 돌려준다.

    영상 DTO가 `metadata`로 `extra`를 그대로 내보낼 때 부른다.
    옛 행의 `dispatch_error`에는 벤더 이름과 파이썬 예외가 남아 있다.
    다른 키와 원본 dict는 바꾸지 않는다.
    """
    if not extra or "dispatch_error" not in extra:
        return extra
    out = dict(extra)
    said = analysis_error_text(extra.get("dispatch_error"), head=DISPATCH_HEAD)
    if said:
        out["dispatch_error"] = said
    else:
        out.pop("dispatch_error", None)
    return out


#: 분석 서비스 일시 오류 종류별 짧은 이름.
#: 요청 대기 행의 보류 사유가 쓴다.
#: 화면은 이 이름을 그대로 표시하고 사유 문장으로 종류를 다시 판정하지 않는다.
ANALYSIS_TRANSIENT_LABEL: dict[str, str] = {
    "throttled": "분석 서비스 요청량 제한",
    "timeout": "분석 서비스 응답 지연",
    "server_error": "분석 서비스 오류",
    "unreachable": "분석 서비스 연결 실패",
    "other": "분석 서비스 일시 오류",
}


def analysis_transient_kind(status: int | None) -> str:
    """분석 서비스 일시 오류의 종류를 HTTP 상태 코드로 정한다.

    상태 코드가 없으면 응답을 받지 못한 경우(연결 실패·시간 초과)다. 사유 문장은 벤더 본문과
    번역이 섞여 바뀔 수 있으므로 종류는 상태 코드로만 정한다.

    Args:
        status: 분석 서비스가 돌려준 상태 코드(`MaiuTransientError.status`). 응답이 없으면 `None`.

    Returns:
        `ANALYSIS_TRANSIENT_LABEL`의 키 하나.
    """
    if status is None:
        return "unreachable"
    if status == 429:
        return "throttled"
    if status in (408, 425):
        return "timeout"
    if status >= 500:
        return "server_error"
    return "other"


#: 일시 오류로 보내지 못한 분석 요청의 보류 표시 키(`videos.metadata`).
#: 값은 `{at, reason, kind, retry_by, scene_options}` 키의 사전이다.
#: `maiu.service`가 쓰고 영상 DTO가 읽는다.
DISPATCH_DEFERRED_KEY = "dispatch_deferred"


def dispatch_deferred_view(extra: dict | None) -> dict | None:
    """영상 `extra`의 보류 표시를 화면에 내보낼 값으로 바꾼다. 보류 표시가 없으면 `None`을 돌려준다.

    종류(`kind`)가 없는 옛 표시에는 일반 이름(`other`의 이름)을 붙인다. 다음 재요청 때 표시가
    새로 기록된다.
    """
    mark = (extra or {}).get(DISPATCH_DEFERRED_KEY)
    if not isinstance(mark, dict):
        return None
    kind = mark.get("kind")
    return {
        "kind": kind,
        "label": ANALYSIS_TRANSIENT_LABEL.get(kind or "other", ANALYSIS_TRANSIENT_LABEL["other"]),
        "reason": mark.get("reason") or None,
        "at": mark.get("at"),
        "retry_by": mark.get("retry_by"),
    }
