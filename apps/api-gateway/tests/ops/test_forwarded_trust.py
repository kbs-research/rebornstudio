"""운영 형상이 클라이언트 주소와 https 여부를 꾸밀 수 없게 막는지 보는 테스트.

두 겹으로 막는다.

1. web nginx는 `X-Forwarded-For`를 연결한 주소 하나로 덮어쓰고, `X-Forwarded-Proto`는 믿는 앞단
   (`WEB_TRUSTED_PROXIES`)이 보낸 값만 넘긴다. 값이 비면 아무도 믿지 않는다.
2. api는 web과 api만 붙는 edge 서브넷에서 온 헤더만 믿는다. 같은 서버의 다른 컨테이너(default 네트워크)가
   꾸민 헤더는 감사 출처, IP 한도 키, 로그인 쿠키의 `Secure`가 되지 않는다.

설정 파일을 읽어 단언하고, 운영 기본 신뢰 대역으로 uvicorn 입구(`ProxyHeadersMiddleware`)를 그대로 만들어 본다.
nginx 쪽 신뢰 설정은 시작 스크립트(`web-proxy-trust.sh`)를 이미지 밖에서 실행해 만든 파일을 읽는다.
"""
from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from api_gateway.services import audit
from api_gateway.services.auth import rate_limit, session_cookie
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

_ROOT = Path(__file__).resolve().parents[4]
_DOCKER = _ROOT / "infra" / "docker"
_PROD = (_ROOT / "infra" / "compose" / "compose.prod.yaml").read_text()
_TRUST_SCRIPT = _DOCKER / "web-proxy-trust.sh"

_needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="sh가 없는 환경")


def _prod_trusted() -> str:
    """운영 compose가 `FORWARDED_ALLOW_IPS`를 비웠을 때 쓰는 기본 신뢰 대역을 읽는다."""
    m = re.search(r"FORWARDED_ALLOW_IPS: \$\{FORWARDED_ALLOW_IPS:-\$\{EDGE_SUBNET:-([^}]+)\}\}", _PROD)
    assert m, "FORWARDED_ALLOW_IPS 기본값이 edge 서브넷을 따르지 않는다"
    return m.group(1)


def _service(name: str) -> str:
    """운영 compose에서 서비스 하나의 정의 블록을 잘라 돌려준다."""
    m = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|^\S)", _PROD, re.M | re.S)
    assert m, name
    return m.group(1)


def test_prod_trusts_only_the_edge_subnet():
    """운영 기본 신뢰 대역이 edge 서브넷 하나이고, web은 edge에만, api는 default와 edge에 붙는지 확인한다."""
    trusted = _prod_trusted()
    net = ipaddress.ip_network(trusted)
    assert net.prefixlen >= 24, f"신뢰 대역이 넓다: {trusted}"
    assert f"- subnet: ${{EDGE_SUBNET:-{trusted}}}" in _PROD, "edge 서브넷과 신뢰 대역이 다르다"
    assert "networks: [edge]" in _service("web")
    assert "networks: [default, edge]" in _service("api")
    for worker in ("worker", "worker-qc", "worker-gpu"):
        assert "edge" not in _service(worker), worker


def test_web_receives_the_front_proxy_setting():
    """운영 web이 앞단 신뢰 목록을 받고 기본값이 비어 있는지 확인한다."""
    assert "WEB_TRUSTED_PROXIES: ${WEB_TRUSTED_PROXIES:-}" in _service("web")


async def _seen(peer: tuple[str, int], headers: dict[str, str]) -> dict[str, object]:
    """운영 기본 신뢰 대역의 입구를 거친 요청이 앱에서 어떤 주소·한도 키·쿠키 판정으로 보이는지 돌려준다."""
    seen: dict[str, object] = {}

    async def app(scope, receive, send):
        """요청이 본 값을 기록하고 빈 200을 돌려준다."""
        req = Request(scope)
        seen["ip"] = audit.origin(req)["ip"]
        seen["key"] = rate_limit.ip_key(req)
        seen["secure"] = session_cookie.cookie_secure(req)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    edge = ProxyHeadersMiddleware(app, trusted_hosts=_prod_trusted())
    async with AsyncClient(transport=ASGITransport(app=edge, client=peer),
                           base_url="http://api") as c:
        await c.get("/", headers=headers)
    return seen


def _edge_host(n: int) -> str:
    """edge 서브넷의 n번째 주소를 돌려준다(web 컨테이너 자리)."""
    return str(list(ipaddress.ip_network(_prod_trusted()).hosts())[n])


async def test_web_forwarded_values_reach_audit_limit_and_cookie():
    """web(edge 주소)이 넘긴 사용자 주소와 https가 감사 출처, IP 한도 키, 쿠키 `Secure`가 되는지 확인한다."""
    seen = await _seen((_edge_host(1), 40000),
                       {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"})
    assert seen == {"ip": "203.0.113.9", "key": "ip:203.0.113.9", "secure": True}


@pytest.mark.parametrize("peer", ["172.18.0.5", "172.17.0.1", "10.0.0.7"])
async def test_spoofed_headers_from_an_untrusted_source_are_ignored(peer: str):
    """edge 밖(default 네트워크의 워커, 호스트 브리지)에서 꾸민 주소와 https가 반영되지 않는지 확인한다."""
    seen = await _seen((peer, 40001), {"X-Forwarded-For": "198.51.100.66", "X-Forwarded-Proto": "https"})
    assert seen == {"ip": peer, "key": f"ip:{peer}", "secure": False}


def test_nginx_overwrites_forwarded_for_and_gates_forwarded_proto():
    """nginx가 `X-Forwarded-For`를 덮어쓰고, `X-Forwarded-Proto`는 믿는 앞단의 값만 넘기는지 확인한다."""
    confs = {p.name: p.read_text() for p in _DOCKER.glob("web-nginx*")}
    for name, text in confs.items():
        live = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        assert "$proxy_add_x_forwarded_for" not in live, name
    proxy = confs["web-nginx-api-proxy.conf"]
    assert re.search(r"^proxy_set_header X-Forwarded-For \$remote_addr;$", proxy, re.M)
    assert re.search(r"^proxy_set_header X-Forwarded-Proto \$forwarded_proto;$", proxy, re.M)
    main = confs["web-nginx.conf"]
    assert "include /etc/nginx/generated/proxy-trust.conf;" in main
    block = re.search(r'map "\$from_trusted_proxy:\$http_x_forwarded_proto" \$forwarded_proto \{(.*?)\}',
                      main, re.S)
    assert block, "X-Forwarded-Proto가 믿는 앞단 여부로 갈리지 않는다"
    rules = [line.split() for line in block.group(1).strip().splitlines()]
    assert ["default", "$scheme;"] in rules
    assert all(r[0] == "default" or r[0].startswith('"~*^1:') for r in rules), rules


def _render(tmp_path: Path, value: str | None, host: str = "172.31.250.1") -> subprocess.CompletedProcess:
    """시작 스크립트를 `WEB_TRUSTED_PROXIES=value`로 실행해 신뢰 설정 파일을 `tmp_path`에 만든다."""
    env = {k: v for k, v in os.environ.items() if k != "WEB_TRUSTED_PROXIES"}
    if value is not None:
        env["WEB_TRUSTED_PROXIES"] = value
    return subprocess.run(["sh", str(_TRUST_SCRIPT), str(tmp_path / "proxy-trust.conf"), host],
                          env=env, capture_output=True, text=True, check=False)


@_needs_sh
@pytest.mark.parametrize("value", [None, "", "  "])
def test_empty_front_proxy_list_trusts_nobody(tmp_path, value):
    """값이 없으면 실주소 치환을 하지 않고 모든 연결을 믿지 않는 앞단으로 보는지 확인한다."""
    r = _render(tmp_path, value)
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "proxy-trust.conf").read_text()
    assert "set_real_ip_from" not in text and "real_ip_header" not in text
    assert re.search(r"geo \$realip_remote_addr \$from_trusted_proxy \{\s*default 0;\s*\}", text)


@_needs_sh
def test_listed_front_proxies_and_host_are_trusted(tmp_path):
    """적은 대역과 `host`(기본 게이트웨이)만 실주소 치환과 scheme 신뢰 대상이 되는지 확인한다."""
    r = _render(tmp_path, "10.0.0.0/24, HOST\tfd00::1")
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "proxy-trust.conf").read_text()
    trusted = ["10.0.0.0/24", "172.31.250.1", "fd00::1"]
    assert re.findall(r"^set_real_ip_from (\S+);$", text, re.M) == trusted
    assert re.findall(r"^    (\S+) 1;$", text, re.M) == trusted
    assert "real_ip_header X-Forwarded-For;" in text and "real_ip_recursive on;" in text


@_needs_sh
@pytest.mark.parametrize("bad", ["0.0.0.0/0", "::/0", "*", "example.com", "10.0.0.1; allow all",
                                 "10.0.0.0/33", "10.0.0.1/24/1"])
def test_malformed_front_proxy_fails_and_writes_nothing(tmp_path, bad):
    """형식이 틀리거나 모든 주소를 믿는 값이면 실패로 끝나고 설정 파일을 만들지 않는지 확인한다."""
    r = _render(tmp_path, bad)
    assert r.returncode != 0
    assert "WEB_TRUSTED_PROXIES" in r.stderr
    assert not (tmp_path / "proxy-trust.conf").exists()
