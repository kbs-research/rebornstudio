"""외부 호출 오류의 일시·영구 판정. 판정 기준을 이 한 곳에 둔다.

429·5xx·타임아웃은 재시도 대상이다. 일시 오류를 영구 실패로 세면 오래 걸리는 작업일수록
실패로 끝난다. 판정 기준이 여러 곳에 있으면 고칠 때 한 곳만 바뀐다. 그래서 이 모듈을 기준으로
두고 HTTP 응답·boto3 예외 같은 맥락마다 얇은 어댑터를 둔다.

일시 오류로 보는 것은 다음과 같다.

- `408` 요청 타임아웃, `425` too early: 타이밍 문제다.
- `429` 스로틀: 잠시 뒤 재시도하면 성공할 수 있다.
- `500`·`502`·`503`·`504`: 서버 측 문제다.
- 연결 오류(`EndpointConnectionError` 등): 네트워크 순단이다.

`TRANSIENT_STATUS`에서 `403`은 뺀다. 서버가 자격증명으로 직접 호출할 때 403은 권한 문제다.
403을 일시 오류로 보면 끝없이 재시도하면서 원인을 가린다. presigned URL로 읽고 쓰는 경로에서는
URL 만료가 403으로 온다. 그래서 그 경로만 403을 일시 오류로 본다
(워커 업로드와 `is_transient_ffmpeg`).
"""
from __future__ import annotations

import re

from botocore.exceptions import BotoCoreError, ClientError

#: 일시 오류로 보는 HTTP 상태. 403은 뺀다(모듈 docstring 참고).
TRANSIENT_STATUS: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

#: boto3 응답이 스로틀·용량 부족을 오류 코드로만 나타낼 때의 코드. 상태 코드가 400인 경우도 있다.
_TRANSIENT_CODES: frozenset[str] = frozenset({
    "Throttling", "ThrottlingException", "ThrottledException",
    "RequestThrottled", "RequestThrottledException", "SlowDown",
    "RequestTimeout", "RequestTimeoutException",
    "ServiceUnavailable", "InternalError", "TooManyRequestsException",
})


def is_transient_status(status: int | None) -> bool:
    """HTTP 상태 코드가 일시 오류이면 True를 돌려준다. `None`(상태 없음)은 영구 오류로 본다."""
    return status is not None and status in TRANSIENT_STATUS


#: ffmpeg가 원격 입력을 읽다 실패했을 때 stderr에 남기는 문구. stderr에 상태 코드가 없을 때 쓴다.
_FFMPEG_TRANSIENT_PHRASES: tuple[str, ...] = (
    "connection timed out", "connection reset", "connection refused",
    "network is unreachable", "temporary failure in name resolution",
    "operation timed out", "input/output error", "end of file",
    "server returned 5xx",
)

_HTTP_IN_STDERR = re.compile(r"(?:server returned|http error)\s+(\d{3})")


def is_transient_ffmpeg(stderr: str) -> bool:
    """ffmpeg의 stderr가 일시 오류를 뜻하면 True를 돌려준다.

    NCP 대역폭이 낮아 원본 전체를 내려받기 어려우므로 썸네일 워커는 presigned URL을 ffmpeg에
    직접 넘긴다. 그래서 네트워크 순단·스로틀·URL 만료가 정상 경로에서도 생긴다. ffmpeg는 이
    오류들을 모두 0이 아닌 종료 코드 하나로 알리므로 종료 코드로는 코덱이 깨진 파일과 구분할 수
    없다. 그래서 stderr 문구로 구분한다. 둘을 같이 세면 일시 오류가 영구 실패가 된다.

    이 경로의 403은 presigned URL 만료이므로 일시 오류로 본다(`job_runner._HTTP_TRANSIENT`도
    같은 이유로 403을 넣는다). 서버가 자격증명으로 직접 호출할 때의 403은 권한 문제라서
    `TRANSIENT_STATUS`에 넣지 않는다.

    판정할 수 없는 오류는 영구 오류로 본다. 알려지지 않은 오류를 재시도하면 끝나지 않는 작업이
    생긴다.
    """
    low = (stderr or "").lower()
    m = _HTTP_IN_STDERR.search(low)
    if m:
        code = int(m.group(1))
        return code == 403 or is_transient_status(code)
    return any(ph in low for ph in _FFMPEG_TRANSIENT_PHRASES)


def is_transient_boto(exc: BaseException) -> bool:
    """boto3·botocore 예외가 일시 오류이면 True를 돌려준다.

    `ClientError`는 상태 코드와 오류 코드를 모두 확인한다. NCP는 스로틀을 400과 오류 코드로
    돌려주는 경우가 있어 상태 코드만 확인하면 스로틀을 영구 오류로 판정한다.
    `BotoCoreError`(연결·타임아웃 계열)는 응답 자체가 없으므로 모두 일시 오류로 본다.
    """
    if isinstance(exc, ClientError):
        meta = exc.response.get("ResponseMetadata", {})
        if is_transient_status(meta.get("HTTPStatusCode")):
            return True
        return exc.response.get("Error", {}).get("Code") in _TRANSIENT_CODES
    return isinstance(exc, BotoCoreError)
