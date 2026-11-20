# web 이미지. SPA 정적 서빙과 /api 엣지 프록시를 맡는다.
#
# 이 이미지가 same-origin 구성을 구현한다. FE는 `/api` 상대 경로만 쓰고, 여기 nginx가
# `/api/*`를 api 서비스로 스트립 프록시한다. 규칙은 vite dev proxy와 같다. 그래서 CORS가 필요 없다.
#
# 정적 파일과 프록시뿐이라 무상태다. 레플리카 수를 자유롭게 늘릴 수 있다.

# ── 빌드 스테이지 ─────────────────────────────────────────────────────────────
FROM node:22-slim AS build

# pnpm 워크스페이스. web은 @reborn/contracts(타입 전용 패키지)를 workspace로 참조한다.
RUN corepack enable
WORKDIR /app

# 락파일을 고정한다. 락파일이 없으면 `pnpm install`이 빌드할 때마다 다른 버전을 받아
# 호스트와 다른 이미지가 만들어진다.
#
# --frozen-lockfile: 락파일과 package.json이 어긋나면 조용히 다른 버전을 받지 않고 빌드가 실패한다.
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json ./apps/web/
COPY packages/reborn-contracts/typescript/package.json ./packages/reborn-contracts/typescript/
RUN pnpm install --frozen-lockfile

COPY packages/reborn-contracts/typescript/ ./packages/reborn-contracts/typescript/
COPY apps/web/ ./apps/web/
RUN pnpm --filter @reborn/web build

# ── 서빙 스테이지 ─────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# nginx를 이미지의 nginx 사용자(uid·gid 101)로 실행한다. 인터넷 요청을 직접 받는 컨테이너라서, nginx가
# 침해돼도 컨테이너 안 root 권한을 얻지 못하게 한다. 80은 컨테이너 네트워크의 비특권 포트 시작값(0)으로 연다
# (compose.prod.yaml `sysctls`). 비 root로 돌리려면 셋이 더 필요하다.
#   pid 파일을 /tmp에 쓴다 · 임시 파일 폴더(/var/cache/nginx)를 그 사용자에게 준다 ·
#   시작 스크립트가 만드는 설정은 그 사용자의 폴더(/etc/nginx/generated)에 쓴다.
# 나머지 설정 파일은 root 소유라 실행 사용자가 고쳐 쓰지 못한다. `user` 지시어는 비 root에서 뜻이 없어 지운다.
# 이미지의 nginx.conf가 바뀌어 치환이 맞지 않으면 grep이 빌드를 멈춘다. 맞지 않은 채 넘어가면 시작에서 실패한다.
RUN sed -i -e '/^user /d' -e 's,^pid .*;$,pid /tmp/nginx.pid;,' /etc/nginx/nginx.conf \
    && grep -q '^pid /tmp/nginx.pid;$' /etc/nginx/nginx.conf \
    && ! grep -q '^user ' /etc/nginx/nginx.conf \
    && chown -R 101:101 /var/cache/nginx \
    && install -d -o 101 -g 101 /etc/nginx/generated

COPY infra/docker/web-nginx.conf /etc/nginx/conf.d/default.conf
COPY infra/docker/web-nginx-api-proxy.conf /etc/nginx/snippets/api-proxy.conf
# 보안 헤더는 템플릿에 저장소 출처를 넣어 만든다(`web-csp.sh`). 시작할 때 CSP_STORAGE_ORIGINS로 다시 만들고,
# 빌드에서도 기본값으로 한 번 만들어 둔다. 시작 스크립트를 거치지 않고 nginx를 띄워도 헤더 파일이 있다.
COPY infra/docker/web-nginx-headers.conf.template /etc/nginx/reborn/security-headers.conf.template
COPY --chmod=755 infra/docker/web-csp.sh /docker-entrypoint.d/35-reborn-csp.sh
# 앞단 프록시 신뢰 설정도 시작할 때 WEB_TRUSTED_PROXIES로 다시 만들고, 빌드에서는 아무도 믿지 않는 기본값으로 만들어 둔다.
COPY --chmod=755 infra/docker/web-proxy-trust.sh /docker-entrypoint.d/36-reborn-proxy-trust.sh
COPY --from=build /app/apps/web/dist /usr/share/nginx/html

USER 101:101
RUN /docker-entrypoint.d/35-reborn-csp.sh && /docker-entrypoint.d/36-reborn-proxy-trust.sh

EXPOSE 80
# nginx 기본 CMD를 쓴다. 설정은 conf.d/default.conf와 그 파일이 include하는 snippets/·generated/다.
# generated/의 보안 헤더와 앞단 신뢰 설정은 시작할 때 `web-csp.sh`와 `web-proxy-trust.sh`가 만든다.
