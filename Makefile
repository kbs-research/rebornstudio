.PHONY: dev-up dev-down dev-logs dev-ps dev-migrate lockfile verify verify-all test lint hooks-install hooks-uninstall \
	check-utilities contracts openapi openapi-serve seed-dev seed-volume seed-volume-clear dev-token

# 로컬 compose. 공통 형상(compose.yaml) 위에 개발 덮어쓰기(compose.dev.yaml)를 얹는다.
# 켤 프로필은 운영과 같은 규칙(infra/deploy/profiles.sh)으로 루트 .env가 정한다.
# 예: .env에 BACKUP_BUCKET이 있으면 pg-backup도 뜬다(운영은 늘 뜬다). down·logs도 같은 프로필로 불러야
# 프로필로 띄운 컨테이너까지 내리고 본다.
# 호스트에 여는 포트. 같은 기계에서 다른 스택이 같은 포트를 쓰고 있으면 바꿔서 띄운다.
#   make dev-up DEV_WEB_PORT=3480 DEV_API_PORT=8480 DEV_PG_PORT=5480 DEV_REDIS_PORT=6480
# compose는 이 값을 환경변수로 읽는다. 루트 .env는 compose 보간에 쓰이지 않으므로 거기에 적어도 바뀌지 않는다.
DEV_WEB_PORT ?= 3000
DEV_API_PORT ?= 8000
DEV_PG_PORT ?= 5433
DEV_REDIS_PORT ?= 6380
export DEV_WEB_PORT DEV_API_PORT DEV_PG_PORT DEV_REDIS_PORT

LOCAL_COMPOSE = . infra/deploy/profiles.sh && docker compose -f infra/compose/compose.yaml -f infra/compose/compose.dev.yaml $$(compose_profiles .env)

dev-up:
	@. infra/deploy/profiles.sh && p="$$(compose_profiles .env | sed 's/--profile //g')" && echo "켜진 프로필: $${p:-없음}"
	@# --remove-orphans: compose 파일에서 없어진 서비스의 컨테이너를 내린다. beat를 품은 옛 워커 컨테이너가
	@# 남으면 beat가 둘이 되어 주기 작업이 두 번 실행된다.
	$(LOCAL_COMPOSE) up -d --remove-orphans
	@echo "✅ Stack up — 화면: http://localhost:$(DEV_WEB_PORT)  ·  API 문서: http://localhost:$(DEV_API_PORT)/docs"
	@# 마이그레이션은 운영처럼 별도 단계다. 새 DB에서 빠뜨리면 워커가 UndefinedTable 로그만 남긴다.
	@echo "   새 DB면 make dev-migrate, make seed-dev, make dev-token 순서로 이어 갑니다 (infra/compose/README.md «처음 띄울 때»)"

lockfile:
	@# 호스트에 pnpm이 있든 없든 같은 결과가 나오게 고정 컨테이너에서 돈다.
	@# packageManager 필드가 pnpm 버전을 고정하므로 corepack이 그 버전을 쓴다.
	docker run --rm -v "$(PWD):/app" -w /app node:22-slim \
		sh -c "corepack enable && pnpm install --lockfile-only"

dev-down:
	$(LOCAL_COMPOSE) down

dev-logs:
	$(LOCAL_COMPOSE) logs -f

dev-ps:
	$(LOCAL_COMPOSE) ps

# 마이그레이션 one-shot을 컨테이너로 돌린다. 테스트 DB는 덮지 않는다(host alembic을 쓴다).
dev-migrate:
	$(LOCAL_COMPOSE) --profile ops run --rm migrate

# ── CI 미러 ────────────────────────────────────────────────────────────────
# CI가 볼 것을 여기서 먼저 본다. CI 실행 한 번은 수 분이 걸리고, 실패를 보고 고쳐 다시 푸시하면
# 그 왕복을 모두 기다려야 하므로 로컬에서 먼저 거른다. Actions 실행 시간이 과금되는 저장소에서는
# 왕복마다 비용도 든다(잡 하나가 최소 1분).
#
#   make verify       CI의 lint 잡 + 계약 drift (DB 불요)
#   make verify-all   위 + pytest (전용 DB. conftest의 기본 테스트 DB는 쓰지 않는다)
#
# 명령은 ci.yml과 같다. 여기를 고치면 ci.yml도 같이 고친다.
verify:
	@scripts/ci/verify.sh

verify-all:
	@scripts/ci/verify.sh --all

# 익숙한 이름의 별칭. 검사 목록은 verify.sh 한 곳에만 둔다. 여기에 따로 적으면
# CI에 없는 검사나 없는 경로를 도는 식으로 CI와 달라진다.
lint: verify
test: verify-all

# 푸시 전에 `make verify`가 자동으로 돌게 한다. 직접 설치할 때만 걸린다.
# `--no-verify`로 건너뛸 수 있다(급할 때).
hooks-install:
	@mkdir -p .git/hooks
	@printf '#!/bin/sh\nexec make verify\n' > .git/hooks/pre-push
	@chmod +x .git/hooks/pre-push
	@echo "✅ pre-push 훅 설치 — 푸시 전에 make verify가 돕니다 (건너뛰려면 --no-verify)"

hooks-uninstall:
	@rm -f .git/hooks/pre-push && echo "✅ pre-push 훅 제거"

check-utilities:
	@# 소스가 쓰는 Tailwind 유틸이 빌드 CSS에 실제로 생성되는지 본다.
	@# 없는 유틸은 조용히 무시되고 tsc도 build도 잡지 않는다.
	cd apps/web && npx vite build >/dev/null && node scripts/check-utilities.mjs

contracts:
	@# OpenAPI JSON → TypeScript types. pnpm이 있으면 workspace script, 없으면 npx를 쓴다.
	@if command -v pnpm > /dev/null; then \
		pnpm --filter @reborn/contracts gen; \
	else \
		npx -y openapi-typescript docs/api/openapi.json -o packages/reborn-contracts/typescript/src/api.ts; \
	fi
	@echo "✅ packages/reborn-contracts/typescript/src/api.ts"

openapi:
	uv run python scripts/dev/gen_openapi.py
	@echo "✅ docs/api/openapi.json"

openapi-serve:
	npx @redocly/cli preview-docs docs/api/openapi.json --port 8080

seed-dev:
	@# super_admin은 마이그레이션이 아니라 여기서 심는다.
	@# 그 편의가 마이그레이션에 있으면 운영에도 그대로 실린다.
	cd apps/api-gateway && \
	  DATABASE_URL="postgresql+asyncpg://reborn:reborn@localhost:$(DEV_PG_PORT)/rebornstudio" \
	  uv run --package api-gateway python ../../scripts/dev/seed_admin.py
	@# 로컬 DB에 라벨링 워크벤치 개발용 프로젝트·영상·클립·work_item을 시드한다. 다시 실행해도 안전하다.
	@# STORAGE_CONNECTION=<NCP 저장소 연결 이름>을 주면 그 연결로 샘플 영상을 올리고 프로젝트 저장소로 기록한다.
	cd apps/api-gateway && \
	  DATABASE_URL="postgresql+asyncpg://reborn:reborn@localhost:$(DEV_PG_PORT)/rebornstudio" \
	  uv run --package api-gateway python ../../scripts/dev/seed_dev_clip.py \
	    $(if $(STORAGE_CONNECTION),--storage-connection "$(STORAGE_CONNECTION)")

dev-token:
	@# 로컬 화면 검증용 세션을 발급한다. 비밀번호 경로를 쓰지 않는다. 근거는 scripts/dev/dev_token.py에 있다.
	@# 출력 한 줄을 브라우저 콘솔(http://localhost:3000)에 붙여 넣으면 refresh 쿠키를 심고 새로고침한다.
	@# EMAIL=a@example.com 을 주면 그 계정으로 발급한다(역할별 화면 검증).
	@# ACCESS=1 을 주면 API 스크립트용 access 토큰만 한 줄로 낸다.
	@# config가 저장소 루트 .env를 절대 경로로 읽으므로 실행 위치와 무관하다.
	@# access가 15분으로 찍히면 .env를 읽지 못한 것이다(REBORN_ENV_FILE 확인).
	DATABASE_URL="postgresql+asyncpg://reborn:reborn@localhost:$(DEV_PG_PORT)/rebornstudio" \
	  uv run --package api-gateway python scripts/dev/dev_token.py $(if $(EMAIL),--email $(EMAIL)) $(if $(ACCESS),--access)

seed-volume:
	@# 단계 화면(반입~검수)을 운영 밀도로 채운다(영상 43, 클립과 작업 수백 건). 다시 실행해도 안전하다.
	@# '분석 중'을 보려면 워커를 잠시 멈춘다: docker stop essential-worker-1
	uv run --package api-gateway python scripts/dev/seed_dev_volume.py

seed-volume-clear:
	uv run --package api-gateway python scripts/dev/seed_dev_volume.py --clear
