"""브라우저의 CSP 위반 보고를 받는 라우터.

운영 web nginx의 CSP가 `report-uri /api/csp-report`와 `report-to csp`로 이 경로를 가리킨다.
브라우저는 로그인 정보 없이 보고를 보내므로 인증을 걸지 않는다. 대신 주소별 요청 한도와
본문 크기 상한을 두고, 받은 값은 요약만 운영 로그에 남긴다(`services.ops.csp_report`).
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from api_gateway.config import settings
from api_gateway.services.auth import rate_limit
from api_gateway.services.ops import csp_report

router = APIRouter(tags=["system"])


async def csp_report_limiter(request: Request) -> None:
    """CSP 위반 보고 수집 요청을 IP별 60초 윈도로 센다. test 환경에서는 건너뛴다.

    인증 없는 경로라 한도가 없으면 누구나 운영 로그를 채울 수 있다.
    로그인·갱신과 버킷을 나눈다. 위반이 많은 화면 하나가 보고를 몰아 보내도 로그인을 막지 않는다.
    주소 판정과 카운터는 인증 경로와 같은 제한기(`rate_limit`)를 쓴다.
    """
    if settings.environment == "test":
        return
    await rate_limit._get_limiter("csp", settings.rate_limit_csp_report_per_min).hit(
        rate_limit._client_key(request))


async def _read_capped(request: Request) -> bytes:
    """본문을 `MAX_BODY_BYTES`까지만 읽는다.

    Content-Length가 없거나 틀린 요청도 읽는 도중 한도를 넘으면 멈춘다.

    Raises:
        HTTPException: 413. 본문이 한도를 넘는 경우.
    """
    limit = csp_report.MAX_BODY_BYTES
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        csp_report.record_too_large(int(declared))
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            f"request_body_too_large (max {limit} bytes)")
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > limit:
            csp_report.record_too_large(len(buf))
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                                f"request_body_too_large (max {limit} bytes)")
    return bytes(buf)


@router.post(
    "/csp-report",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="CSP 위반 보고 수집 (인증 없음)",
    responses={
        400: {"description": (
            "csp_report_invalid: 두 보고 형식"
            "(application/csp-report·application/reports+json)이 아님"
        )},
        413: {"description": "request_body_too_large: 본문이 64KiB를 넘음"},
        429: {"description": "rate_limit_exceeded:csp: 주소별 분당 한도 초과"},
    },
)
async def receive_csp_report(
    request: Request, _limit: None = Depends(csp_report_limiter),
) -> Response:
    """CSP 위반 보고를 받아 운영 로그에 요약을 남기고 204로 답한다.

    막힌 주소는 출처만, 위반이 난 화면은 경로만 남긴다.
    저장소 presigned 주소의 서명이 로그에 남지 않는다.

    Raises:
        HTTPException: 400 `csp_report_invalid`. 본문 형식이 두 보고 형식이 아닌 경우.
    """
    raw = await _read_capped(request)
    try:
        violations = csp_report.parse(request.headers.get("content-type"), raw)
    except csp_report.InvalidReportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "csp_report_invalid") from exc
    csp_report.record(violations)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
