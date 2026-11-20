#!/bin/sh
# web 컨테이너가 nginx를 띄우기 전에 앞단 프록시 신뢰 설정을 만든다. nginx 공식 이미지가 /docker-entrypoint.d에서 부른다.
#
# WEB_TRUSTED_PROXIES(공백이나 쉼표로 구분)에 적은 주소에서 온 연결만 X-Forwarded-For와 X-Forwarded-Proto를 믿는다.
# 값 하나는 IPv4·IPv6 주소나 CIDR이다. `host`는 이 컨테이너의 기본 게이트웨이, 곧 도커 호스트 주소다.
# 같은 서버의 호스트 프록시가 127.0.0.1:80으로 넘기면 web은 그 연결을 게이트웨이 주소에서 온 것으로 본다.
# 값이 비면 아무도 믿지 않는다. 그때 api에 넘기는 사용자 주소는 web에 연결한 주소이고, scheme은 web이 받은 scheme이다.
# 사용자가 직접 보낸 X-Forwarded-Proto: https가 로그인 쿠키의 Secure와 출처 판정을 바꾸지 못하게 한다.
#
# 형식이 틀린 값이 하나라도 있으면 실패로 끝나 컨테이너가 뜨지 않는다. 모든 주소를 믿는 /0도 받지 않는다.
# 시작 실패는 배포 스크립트의 web 헬스 확인이 바로 잡고 로그에 이유가 남는다.
#
# 인자: [출력 경로] [host 주소]. 이미지 밖에서 같은 스크립트를 시험할 때 넘긴다.
set -eu
set -f

OUT="${1:-/etc/nginx/generated/proxy-trust.conf}"
HOST_ADDR="${2:-}"
IPV4_RE='^([0-9]{1,3}\.){3}[0-9]{1,3}(/([0-9]|[12][0-9]|3[0-2]))?$'
IPV6_RE='^[0-9a-f]*:[0-9a-f:.]*(/([0-9]|[1-9][0-9]|1[01][0-9]|12[0-8]))?$'

host_address() {
  if [ -n "$HOST_ADDR" ]; then
    printf '%s\n' "$HOST_ADDR"
    return
  fi
  ip route show default 2>/dev/null | awk '$1 == "default" && $2 == "via" { print $3; exit }'
}

raw="$(printf '%s' "${WEB_TRUSTED_PROXIES:-}" | tr ',\t\r\n' '    ' | tr 'A-Z' 'a-z')"
proxies=""
for p in $raw; do
  if [ "$p" = "host" ]; then
    p="$(host_address)"
    if [ -z "$p" ]; then
      echo "web-proxy-trust: WEB_TRUSTED_PROXIES의 host 주소(기본 게이트웨이)를 찾지 못했다" >&2
      exit 1
    fi
  fi
  case "$p" in
    */0)
      echo "web-proxy-trust: WEB_TRUSTED_PROXIES의 '$p'는 모든 주소를 믿는다" >&2
      exit 1
      ;;
  esac
  if ! printf '%s\n' "$p" | grep -Eq "$IPV4_RE" && ! printf '%s\n' "$p" | grep -Eq "$IPV6_RE"; then
    echo "web-proxy-trust: WEB_TRUSTED_PROXIES의 '$p'는 IP 주소나 CIDR이 아니다" >&2
    exit 1
  fi
  proxies="${proxies:+$proxies }$p"
done

mkdir -p "$(dirname "$OUT")"
{
  echo "# web-proxy-trust.sh가 WEB_TRUSTED_PROXIES로 시작할 때 만드는 파일이다. 손으로 고치지 않는다."
  for p in $proxies; do
    echo "set_real_ip_from $p;"
  done
  if [ -n "$proxies" ]; then
    echo "real_ip_header X-Forwarded-For;"
    echo "real_ip_recursive on;"
  fi
  echo 'geo $realip_remote_addr $from_trusted_proxy {'
  echo "    default 0;"
  for p in $proxies; do
    echo "    $p 1;"
  done
  echo "}"
} > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
echo "web-proxy-trust: 믿는 앞단 ${proxies:-없음}"
