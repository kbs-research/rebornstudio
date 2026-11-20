"""브라우저가 보낸 CSP 위반 보고를 읽어 운영 로그에 요약으로 남긴다.

CSP 강제 모드는 정책 밖의 자원을 막기만 하고 결과를 그 사용자 브라우저 콘솔에만 남긴다.
저장소 출처를 잘못 넣은 배포나 정책에 없는 새 자원은 운영 로그의 위반 줄로 드러난다.

보고 형식은 둘이다. `report-uri`를 쓰는 브라우저는 `application/csp-report`로 보고 하나를,
`report-to`를 쓰는 브라우저는 `application/reports+json`으로 보고 여러 개의 배열을 보낸다.

막힌 주소는 출처(스킴·호스트·포트)만 남긴다. 저장소 presigned 주소의 쿼리에는 서명과
자격증명 id가 있어 그대로 남기면 운영 로그를 읽는 사람이 만료 전까지 그 객체를 받는다.
위반이 난 화면도 경로만 남긴다.
보고 본문은 인증 없이 누구나 보낼 수 있으므로 값은 정해진 문자만 남기고 길이를 자른다.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger("reborn.csp")

#: 받는 본문의 최대 크기(바이트). 보고 하나는 1~2KB이고, 썸네일이 여럿 막힌 화면은
#: `report-to` 배치 하나에 보고 수십 개를 담는다.
MAX_BODY_BYTES = 64 * 1024
#: 요청 하나에서 남기는 서로 다른 위반의 최대 수. 나머지는 수만 한 줄로 남긴다.
MAX_LINES = 20
#: 받는 본문 형식. 매개변수(`; charset=…`)는 떼고 비교한다.
CONTENT_TYPES = frozenset({"application/csp-report", "application/reports+json"})

_DIRECTIVE = re.compile(r"^[a-z-]{1,40}$")
_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")
_KEYWORD = re.compile(r"^[a-z-]{1,30}$")
_UNSAFE = re.compile(r"[^\x21-\x7e]")
_PATH_MAX = 200


class InvalidReportError(ValueError):
    """본문 형식이 정한 두 보고 형식이 아닌 경우."""


@dataclass(frozen=True)
class Violation:
    """운영 로그에 남기는 위반 하나의 요약."""

    directive: str
    blocked: str
    page: str
    disposition: str


def media_type(content_type: str | None) -> str:
    """Content-Type 헤더에서 매개변수를 뗀 소문자 형식을 돌려준다."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def _directive(value: object) -> str:
    """지시어 이름을 돌려준다. 형식이 틀리면 `unknown`이다."""
    v = str(value or "").strip().lower()
    # 옛 브라우저는 violated-directive에 지시어와 출처 목록을 함께 싣는다(예: `img-src 'self'`).
    v = v.split(" ", 1)[0]
    return v if _DIRECTIVE.match(v) else "unknown"


def _blocked(value: object) -> str:
    """막힌 주소의 출처를 돌려준다. 경로·쿼리·조각은 버린다.

    http(s)·ws(s) 주소는 `스킴://호스트[:포트]`, `inline`·`eval`처럼 주소가 아닌 값과 `data`·`blob`
    같은 스킴은 그 낱말만 남긴다. 어느 쪽도 아니면 `other`다.
    """
    raw = str(value or "").strip()
    if not raw:
        return "none"
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return "other"
    scheme = parts.scheme.lower()
    if scheme in ("http", "https", "ws", "wss"):
        host = (parts.hostname or "").lower()
        if not _HOST.match(host):
            return "other"
        return f"{scheme}://{host}" + (f":{port}" if port else "")
    word = (scheme or raw).lower()
    return word if _KEYWORD.match(word) else "other"


def _page(value: object) -> str:
    """위반이 난 문서의 경로를 돌려준다. 출처·쿼리·조각은 버린다."""
    raw = str(value or "").strip()
    try:
        path = urlsplit(raw).path or "/"
    except ValueError:
        return "other"
    return _UNSAFE.sub("?", path)[:_PATH_MAX]


def _disposition(value: object) -> str:
    """강제(`enforce`)인지 보고 전용(`report`)인지 돌려준다."""
    v = str(value or "enforce").strip().lower()
    return v if v in ("enforce", "report") else "other"


def _from_legacy(body: dict) -> Violation:
    """`application/csp-report` 보고 하나를 요약한다."""
    return Violation(
        directive=_directive(body.get("effective-directive") or body.get("violated-directive")),
        blocked=_blocked(body.get("blocked-uri")),
        page=_page(body.get("document-uri")),
        disposition=_disposition(body.get("disposition")),
    )


def _from_reporting(body: dict) -> Violation:
    """`application/reports+json` 보고의 `body` 하나를 요약한다."""
    return Violation(
        directive=_directive(body.get("effectiveDirective") or body.get("violatedDirective")),
        blocked=_blocked(body.get("blockedURL")),
        page=_page(body.get("documentURL")),
        disposition=_disposition(body.get("disposition")),
    )


def parse(content_type: str | None, raw: bytes) -> list[Violation]:
    """보고 본문을 위반 요약 목록으로 바꾼다.

    `reports+json` 배열에서 CSP가 아닌 보고(`deprecation` 등)는 건너뛴다.

    Raises:
        InvalidReportError: 형식이 두 보고 형식이 아니거나 본문이 그 형식의 JSON이 아닌 경우.
    """
    kind = media_type(content_type)
    if kind not in CONTENT_TYPES:
        raise InvalidReportError(kind)
    try:
        doc = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InvalidReportError("json") from exc
    if kind == "application/csp-report":
        body = doc.get("csp-report") if isinstance(doc, dict) else None
        if not isinstance(body, dict):
            raise InvalidReportError("csp-report")
        return [_from_legacy(body)]
    if not isinstance(doc, list):
        raise InvalidReportError("reports")
    return [
        _from_reporting(r["body"])
        for r in doc
        if isinstance(r, dict) and r.get("type") == "csp-violation"
        and isinstance(r.get("body"), dict)
    ]


def record(violations: list[Violation]) -> None:
    """같은 위반을 묶어 수와 함께 운영 로그에 경고로 남긴다.

    서로 다른 위반은 `MAX_LINES`줄까지 남기고 나머지는 수 한 줄로 남긴다.
    """
    counts = Counter(violations)
    for v, n in counts.most_common(MAX_LINES):
        log.warning(
            "csp_violation directive=%s blocked=%s page=%s disposition=%s count=%d",
            v.directive, v.blocked, v.page, v.disposition, n,
        )
    rest = sum(n for _v, n in counts.most_common()[MAX_LINES:])
    if rest:
        log.warning("csp_violation_more count=%d", rest)


def record_too_large(size: int) -> None:
    """한도를 넘어 거절한 보고 본문을 운영 로그에 남긴다.

    큰 배치는 위반이 한꺼번에 많이 났다는 신호라서, 본문을 읽지 않아도 크기만은 남긴다.
    """
    log.warning("csp_report_too_large bytes=%d max=%d", size, MAX_BODY_BYTES)
