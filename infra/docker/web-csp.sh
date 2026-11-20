#!/bin/sh
# web 컨테이너가 nginx를 띄우기 전에 보안 헤더 파일을 만든다. nginx 공식 이미지가 /docker-entrypoint.d에서 부른다.
#
# CSP의 img-src·media-src·connect-src에 넣을 저장소 출처를 CSP_STORAGE_ORIGINS(공백 구분)에서 받는다.
# 값이 비면 운영 저장소인 NCP 객체 저장소 출처를 쓴다. 서버가 만드는 presigned 주소는 경로 방식
# (https://kr.object.ncloudstorage.com/버킷/키)이라 버킷 하위 도메인은 열지 않는다.
#
# 출처 하나는 https://호스트[:포트] 형식이고 호스트 맨 앞에만 *. 와일드카드를 쓸 수 있다.
# 형식이 틀린 값이 하나라도 있으면 실패로 끝나 컨테이너가 뜨지 않는다. 기본값으로 대신 띄우면
# 다른 저장소를 쓰는 배포에서 썸네일·재생·업로드가 브라우저에서만 막혀 화면을 열어 봐야 알게 된다.
# 시작 실패는 배포 스크립트의 web 헬스 확인이 바로 잡고 로그에 이유가 남는다.
# 세미콜론·따옴표·경로가 든 값은 정책에 다른 지시어를 끼워 넣을 수 있어 받지 않는다.
#
# 인자: [템플릿 경로] [출력 경로]. 이미지 밖에서 같은 스크립트를 시험할 때 넘긴다.
set -eu
# 와일드카드 출처(https://*.example.com)가 파일 이름으로 펼쳐지지 않게 한다.
set -f

TEMPLATE="${1:-/etc/nginx/reborn/security-headers.conf.template}"
OUT="${2:-/etc/nginx/generated/security-headers.conf}"
PLACEHOLDER="__CSP_STORAGE_ORIGINS__"
DEFAULT_ORIGINS="https://kr.object.ncloudstorage.com"
ORIGIN_RE='^https://(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:[0-9]{1,5})?$'

raw="$(printf '%s' "${CSP_STORAGE_ORIGINS:-}" | tr '\t\r\n' '   ' | tr 'A-Z' 'a-z')"
origins=""
for o in $raw; do
  if ! printf '%s\n' "$o" | grep -Eq "$ORIGIN_RE"; then
    echo "web-csp: CSP_STORAGE_ORIGINS의 '$o'는 https://호스트[:포트] 형식이 아니다" >&2
    exit 1
  fi
  origins="${origins:+$origins }$o"
done
if [ -z "$origins" ]; then
  origins="$DEFAULT_ORIGINS"
fi

if ! grep -q "$PLACEHOLDER" "$TEMPLATE"; then
  echo "web-csp: 템플릿 $TEMPLATE에 $PLACEHOLDER 자리가 없다" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT")"
sed "s|$PLACEHOLDER|$origins|g" "$TEMPLATE" > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
echo "web-csp: 저장소 출처 $origins"
