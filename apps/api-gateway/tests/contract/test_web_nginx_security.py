"""운영 web nginx 설정이 강제 CSP와 쿼리 없는 접근 로그, 덮어쓰는 X-Forwarded-For를 지키는지 보는 테스트.

설정 파일은 이미지를 빌드해야 실행되므로 서버 테스트가 문자열로 읽어 대조한다. 보고 전용 CSP는
위반을 막지 않고, 쿼리가 남는 접근 로그는 링크 토큰을 남긴다. 받은 X-Forwarded-For에 덧붙이면
앞단 LB가 없는 운영에서 사용자가 보낸 값이 로그인 한도와 감사 기록의 주소가 된다.
CSP의 저장소 출처는 시작 스크립트(`web-csp.sh`)가 배포 설정으로 넣으므로, 스크립트를 이미지 밖에서
실행해 만든 헤더 파일을 읽는다. nginx는 띄우지 않는다.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from api_gateway.services import platform_credentials as pc
from api_gateway.services.media import storage

_DOCKER = Path(__file__).resolve().parents[4] / "infra" / "docker"
_TEMPLATE = _DOCKER / "web-nginx-headers.conf.template"
_SCRIPT = _DOCKER / "web-csp.sh"
_DOCKERFILE = _DOCKER / "web.Dockerfile"
_NCP = "https://kr.object.ncloudstorage.com"
_STORAGE_DIRECTIVES = ("img-src", "media-src", "connect-src")

_needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="sh가 없는 환경")


def _render(tmp_path: Path, origins: str | None) -> subprocess.CompletedProcess:
    """시작 스크립트를 `CSP_STORAGE_ORIGINS=origins`로 실행해 헤더 파일을 `tmp_path`에 만든다."""
    env = {k: v for k, v in os.environ.items() if k != "CSP_STORAGE_ORIGINS"}
    if origins is not None:
        env["CSP_STORAGE_ORIGINS"] = origins
    return subprocess.run(
        ["sh", str(_SCRIPT), str(_TEMPLATE), str(tmp_path / "security-headers.conf")],
        env=env, capture_output=True, text=True, check=False,
    )


def _headers(tmp_path: Path, origins: str | None = None) -> str:
    """기본값이나 `origins`로 만든 헤더 파일 내용을 돌려준다."""
    r = _render(tmp_path, origins)
    assert r.returncode == 0, r.stderr
    return (tmp_path / "security-headers.conf").read_text(encoding="utf-8")


def _csp(text: str) -> dict[str, list[str]]:
    """헤더 파일의 강제 CSP를 지시어별 출처 목록으로 읽는다."""
    assert "Content-Security-Policy-Report-Only" not in text, "CSP가 보고 전용이다"
    found = re.search(r'add_header Content-Security-Policy "([^"]+)" always;', text)
    assert found, "강제 CSP 헤더가 없다"
    out: dict[str, list[str]] = {}
    for part in found.group(1).split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


@_needs_sh
def test_csp_is_enforced_and_scripts_stay_same_origin(tmp_path):
    """CSP가 강제 모드이고 스크립트는 같은 출처만, 프레임과 플러그인은 막는지 확인한다."""
    csp = _csp(_headers(tmp_path))
    assert csp["default-src"] == ["'self'"]
    assert csp["script-src"] == ["'self'"]
    assert csp["object-src"] == ["'none'"]
    assert csp["frame-ancestors"] == ["'none'"]
    assert csp["base-uri"] == ["'self'"]
    assert csp["form-action"] == ["'self'"]


@_needs_sh
def test_csp_opens_only_what_the_web_needs(tmp_path):
    """저장소 출처와 업로드 미리보기(blob), 번들 글꼴(data)만 열고 https 전체는 열지 않는지 확인한다."""
    csp = _csp(_headers(tmp_path))
    assert csp["font-src"] == ["'self'", "data:"]
    assert csp["media-src"] == ["'self'", "blob:", _NCP]
    assert csp["connect-src"] == ["'self'", _NCP]
    assert csp["img-src"] == ["'self'", "data:", "blob:", _NCP]
    assert "'unsafe-eval'" not in sum(csp.values(), [])
    assert "https:" not in sum(csp.values(), [])


@_needs_sh
@pytest.mark.parametrize("origins", [None, "", "   "])
def test_storage_directives_default_to_ncp_origin(tmp_path, origins):
    """설정이 없거나 비면 저장소 지시어 셋이 NCP 객체 저장소 출처 하나만 여는지 확인한다."""
    csp = _csp(_headers(tmp_path, origins))
    for directive in _STORAGE_DIRECTIVES:
        assert [s for s in csp[directive] if s.startswith("https")] == [_NCP], directive


@_needs_sh
def test_storage_directives_take_configured_origins(tmp_path):
    """설정한 출처 목록이 저장소 지시어 셋에 모두 들어가고 기본값을 대신하는지 확인한다.

    대문자는 소문자로 바꾸고, 탭과 여러 칸 공백도 출처 구분으로 받는다.
    """
    csp = _csp(_headers(
        tmp_path, "https://a.example.com\t https://*.B.example.com:8443  " + _NCP))
    want = ["https://a.example.com", "https://*.b.example.com:8443", _NCP]
    for directive in _STORAGE_DIRECTIVES:
        assert [s for s in csp[directive] if s.startswith("https")] == want, directive
    assert csp["script-src"] == ["'self'"]


@_needs_sh
@pytest.mark.parametrize("bad", [
    "https:",
    "*",
    "http://insecure.example.com",
    "https://a.example.com/path",
    "https://a.example.com?x=1",
    "https://a.example.com; script-src *",
    "https://a.example.com;script-src",
    "'unsafe-inline'",
    "https://a.example.com 'unsafe-eval'",
    'https://a.example.com"',
    "https://a.*.example.com",
    "https://*",
    "data:",
])
def test_malformed_origin_fails_and_writes_nothing(tmp_path, bad):
    """형식이 틀린 출처가 하나라도 있으면 실패로 끝나고 헤더 파일을 만들지 않는지 확인한다.

    기본값으로 대신 띄우면 다른 저장소를 쓰는 배포가 화면에서만 깨진다. 세미콜론·따옴표가 든 값은
    정책에 다른 지시어를 끼워 넣는다.
    """
    r = _render(tmp_path, bad)
    assert r.returncode != 0
    assert "CSP_STORAGE_ORIGINS" in r.stderr
    assert not (tmp_path / "security-headers.conf").exists()
    assert not (tmp_path / "security-headers.conf.tmp").exists()


@_needs_sh
def test_rendered_file_has_no_placeholder(tmp_path):
    """만든 헤더 파일에 자리표시가 남지 않고, 템플릿에는 저장소 지시어 셋의 자리표시가 있는지 확인한다."""
    assert _TEMPLATE.read_text(encoding="utf-8").count("__CSP_STORAGE_ORIGINS__") == 3
    assert "__CSP_STORAGE_ORIGINS__" not in _headers(tmp_path)


@_needs_sh
def test_violations_are_reported_to_api(tmp_path):
    """정책이 위반 보고를 같은 출처의 `/api/csp-report`로 보내는지 확인한다.

    `report-to`를 아는 브라우저는 `Reporting-Endpoints`의 끝점을, 모르는 브라우저는 `report-uri`를 쓴다.
    """
    text = _headers(tmp_path)
    csp = _csp(text)
    assert csp["report-uri"] == ["/api/csp-report"]
    assert csp["report-to"] == ["csp"]
    assert re.search(r"""^add_header Reporting-Endpoints 'csp="/api/csp-report"' always;$""", text, re.M)


def test_image_renders_headers_at_build_and_start():
    """이미지가 템플릿과 시작 스크립트를 담고 빌드에서 기본값으로 헤더 파일을 한 번 만드는지 확인한다."""
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert "web-nginx-headers.conf.template /etc/nginx/reborn/security-headers.conf.template" in text
    assert "web-csp.sh /docker-entrypoint.d/35-reborn-csp.sh" in text
    assert "RUN /docker-entrypoint.d/35-reborn-csp.sh" in text
    copies = re.findall(r"^COPY .*$", text, re.M)
    assert not [c for c in copies if c.endswith("security-headers.conf")]


def test_default_origin_covers_presigned_urls():
    """서버가 만드는 presigned 주소의 출처가 기본 저장소 출처와 같은지 확인한다.

    운영 클라이언트 설정(`storage._client`)으로 NCP 주소를 서명하면 경로 방식이라 버킷이 호스트에
    붙지 않는다. botocore가 가상 호스트 방식으로 바꾸면 버킷 하위 도메인이 기본 출처 밖이 되어
    브라우저가 막는다.
    """
    client = storage._client(pc.StorageCredentials(
        endpoint=_NCP, region="kr-standard", bucket="sample-bucket",
        access_key="test", secret_key="test", connection_id="csp-origin-test"))
    urls = [
        client.generate_presigned_url(
            "get_object", Params={"Bucket": "sample-bucket", "Key": "a/b.mp4"}),
        client.generate_presigned_url(
            "upload_part",
            Params={"Bucket": "sample-bucket", "Key": "a/b.mp4", "UploadId": "u", "PartNumber": 1}),
        client.generate_presigned_post("sample-bucket", "a/b.png")["url"],
    ]
    for url in urls:
        assert url.startswith(_NCP + "/sample-bucket"), url


def test_access_log_has_no_query_string():
    """접근 로그 형식이 쿼리를 담은 변수를 쓰지 않고 server 블록이 그 형식을 쓰는지 확인한다."""
    text = (_DOCKER / "web-nginx.conf").read_text()
    fmt = re.search(r"log_format no_query (.+?);", text, re.S)
    assert fmt, "쿼리 없는 접근 로그 형식이 없다"
    for var in ("$request ", "$request\"", "$args", "$query_string", "$request_uri", "$http_referer"):
        assert var not in fmt.group(1), var
    server = text.split("server {", 1)[1]
    assert "access_log /dev/stdout no_query;" in server


def test_forwarded_for_is_overwritten_with_the_peer_address():
    """api 프록시가 받은 X-Forwarded-For에 덧붙이지 않고 연결한 주소로 덮어쓰는지 확인한다."""
    text = (_DOCKER / "web-nginx-api-proxy.conf").read_text()
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in text
    assert "$proxy_add_x_forwarded_for" not in text
    assert "proxy_set_header X-Forwarded-Proto $forwarded_proto;" in text
