"""CSP 위반 보고 수집 경로(`POST /csp-report`)를 확인한다.

브라우저는 로그인 정보 없이 두 형식으로 보고를 보낸다. 인증 없는 경로라 본문 크기와 주소별 요청 수를
막고, 운영 로그에는 막힌 주소의 출처와 화면 경로만 남겨 presigned 서명이 로그에 남지 않게 한다.
"""
import json
import logging

import pytest
from api_gateway.config import settings
from api_gateway.services.auth import rate_limit
from api_gateway.services.ops import csp_report
from httpx import AsyncClient

_SIGNED = (
    "https://kr.object.ncloudstorage.com/sample-bucket/clips/a.jpg"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAEXAMPLE%2F20261013"
    "&X-Amz-Signature=deadbeefcafe"
)
_LEGACY = "application/csp-report"
_REPORTS = "application/reports+json"


@pytest.fixture(autouse=True)
def _reset_limiters():
    """테스트 전후로 요청 한도 상태를 지운다."""
    rate_limit.reset_for_test()
    yield
    rate_limit.reset_for_test()


def _legacy(blocked: str = _SIGNED, directive: str = "img-src") -> bytes:
    """`report-uri` 형식 보고 하나를 만든다."""
    return json.dumps({"csp-report": {
        "document-uri": "https://203.0.113.7/p/demo/videos?tab=clips&token=secret#frag",
        "referrer": "",
        "violated-directive": directive,
        "effective-directive": directive,
        "original-policy": "default-src 'self'",
        "disposition": "enforce",
        "blocked-uri": blocked,
        "status-code": 200,
    }}).encode()


def _reporting(blocked: str, directive: str = "media-src") -> dict:
    """`report-to` 형식 배열의 CSP 보고 하나를 만든다."""
    return {
        "type": "csp-violation", "age": 10, "url": "https://203.0.113.7/work/1/label?x=1",
        "user_agent": "Mozilla/5.0",
        "body": {
            "documentURL": "https://203.0.113.7/work/1/label?x=1",
            "blockedURL": blocked,
            "effectiveDirective": directive,
            "originalPolicy": "default-src 'self'",
            "disposition": "enforce",
            "statusCode": 200,
            "sample": "",
        },
    }


def _lines(caplog) -> list[str]:
    """수집 경로가 남긴 운영 로그 줄을 돌려준다."""
    return [r.getMessage() for r in caplog.records if r.name == "reborn.csp"]


async def test_legacy_report_is_logged_as_origin_only(client: AsyncClient, caplog):
    """`application/csp-report` 보고를 인증 없이 받아 204로 답하고, 출처와 경로만 로그에 남기는지 확인한다."""
    with caplog.at_level(logging.WARNING, logger="reborn.csp"):
        r = await client.post("/csp-report", content=_legacy(), headers={"Content-Type": _LEGACY})
    assert r.status_code == 204, r.text
    assert r.content == b""
    lines = _lines(caplog)
    assert lines == [
        "csp_violation directive=img-src blocked=https://kr.object.ncloudstorage.com "
        "page=/p/demo/videos disposition=enforce count=1"
    ]
    assert all(r.levelno == logging.WARNING for r in caplog.records if r.name == "reborn.csp")


async def test_logged_lines_carry_no_query_or_signature(client: AsyncClient, caplog):
    """두 형식 모두 로그에 쿼리 문자열·서명·자격증명 id·조각이 남지 않는지 확인한다."""
    with caplog.at_level(logging.WARNING, logger="reborn.csp"):
        await client.post("/csp-report", content=_legacy(), headers={"Content-Type": _LEGACY})
        await client.post("/csp-report", content=json.dumps([_reporting(_SIGNED)]),
                          headers={"Content-Type": _REPORTS})
    text = "\n".join(_lines(caplog))
    assert len(_lines(caplog)) == 2
    for leaked in ("?", "X-Amz", "deadbeef", "AKIAEXAMPLE", "secret", "frag", "sample-bucket", "a.jpg"):
        assert leaked not in text, leaked


async def test_reporting_api_batch_is_grouped(client: AsyncClient, caplog):
    """`application/reports+json` 배열에서 CSP 보고만 골라 같은 위반을 수와 함께 한 줄로 남기는지 확인한다."""
    batch = [
        _reporting("https://other.example.com:8443/x.js?k=v", "script-src-elem"),
        _reporting("https://other.example.com:8443/y.js", "script-src-elem"),
        _reporting("inline", "style-src-attr"),
        {"type": "deprecation", "body": {"id": "x"}},
        "garbage",
    ]
    with caplog.at_level(logging.WARNING, logger="reborn.csp"):
        r = await client.post("/csp-report", content=json.dumps(batch),
                              headers={"Content-Type": f"{_REPORTS}; charset=utf-8"})
    assert r.status_code == 204, r.text
    assert sorted(_lines(caplog)) == sorted([
        "csp_violation directive=script-src-elem blocked=https://other.example.com:8443 "
        "page=/work/1/label disposition=enforce count=2",
        "csp_violation directive=style-src-attr blocked=inline "
        "page=/work/1/label disposition=enforce count=1",
    ])


async def test_many_distinct_violations_are_capped(client: AsyncClient, caplog):
    """서로 다른 위반이 많으면 정한 줄 수까지만 남기고 나머지는 수 한 줄로 남기는지 확인한다."""
    n = csp_report.MAX_LINES + 5
    batch = [_reporting(f"https://h{i}.example.com/a.png", "img-src") for i in range(n)]
    with caplog.at_level(logging.WARNING, logger="reborn.csp"):
        r = await client.post("/csp-report", content=json.dumps(batch), headers={"Content-Type": _REPORTS})
    assert r.status_code == 204
    lines = _lines(caplog)
    assert len(lines) == csp_report.MAX_LINES + 1
    assert lines[-1] == "csp_violation_more count=5"


@pytest.mark.parametrize(("content_type", "body"), [
    ("application/json", _legacy()),
    ("text/plain", _legacy()),
    (_LEGACY, b"{not json"),
    (_LEGACY, b'{"other": {}}'),
    (_LEGACY, b"[]"),
    (_REPORTS, b'{"type": "csp-violation"}'),
    (_REPORTS, b"[" * 5000 + b"]" * 5000),
])
async def test_other_bodies_are_rejected(client: AsyncClient, caplog, content_type, body):
    """두 보고 형식이 아닌 본문은 400 `csp_report_invalid`이고 로그에 위반을 남기지 않는지 확인한다."""
    with caplog.at_level(logging.WARNING, logger="reborn.csp"):
        r = await client.post("/csp-report", content=body, headers={"Content-Type": content_type})
    assert r.status_code == 400
    assert r.json()["detail"] == "csp_report_invalid"
    assert _lines(caplog) == []


async def test_oversized_body_is_413(client: AsyncClient, caplog):
    """본문이 상한을 넘으면 읽지 않고 413으로 답하고 크기만 로그에 남기는지 확인한다."""
    body = b'{"csp-report": {"blocked-uri": "' + b"a" * csp_report.MAX_BODY_BYTES + b'"}}'
    with caplog.at_level(logging.WARNING, logger="reborn.csp"):
        r = await client.post("/csp-report", content=body, headers={"Content-Type": _LEGACY})
    assert r.status_code == 413
    assert r.json()["detail"].startswith("request_body_too_large")
    assert _lines(caplog) == [f"csp_report_too_large bytes={len(body)} max={csp_report.MAX_BODY_BYTES}"]


async def test_oversized_stream_without_length_is_413(client: AsyncClient):
    """Content-Length 없이 나눠 보낸 본문도 읽는 도중 상한을 넘으면 413인지 확인한다."""
    chunk = b"a" * 8192

    async def gen():
        """상한보다 큰 본문을 조각으로 내보낸다."""
        for _ in range(csp_report.MAX_BODY_BYTES // len(chunk) + 2):
            yield chunk

    r = await client.post("/csp-report", content=gen(), headers={"Content-Type": _LEGACY})
    assert r.status_code == 413


async def test_body_at_limit_is_accepted(client: AsyncClient):
    """상한과 같은 크기의 본문은 받는지 확인한다."""
    head = b'{"csp-report": {"blocked-uri": "inline", "pad": "'
    tail = b'"}}'
    body = head + b"a" * (csp_report.MAX_BODY_BYTES - len(head) - len(tail)) + tail
    assert len(body) == csp_report.MAX_BODY_BYTES
    r = await client.post("/csp-report", content=body, headers={"Content-Type": _LEGACY})
    assert r.status_code == 204


async def test_reports_are_rate_limited_per_address(client: AsyncClient, monkeypatch):
    """주소별 분당 한도를 넘는 보고는 429 `rate_limit_exceeded:csp`인지 확인한다."""
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "rate_limit_csp_report_per_min", 3)
    codes = []
    for _ in range(4):
        r = await client.post("/csp-report", content=_legacy(), headers={"Content-Type": _LEGACY})
        codes.append(r.status_code)
    assert codes == [204, 204, 204, 429]
    assert r.json()["detail"].startswith("rate_limit_exceeded:csp")
