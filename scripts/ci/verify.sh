#!/usr/bin/env bash
# CI가 도는 검사를 로컬에서 먼저 돌린다(`make verify` / `make verify-all`).
#
# CI 실행 한 번은 수 분이 걸린다. 실패를 보고 고쳐서 다시 푸시하면 그 왕복을 모두 기다려야 하므로
# 로컬에서 먼저 거른다. Actions 실행 시간이 과금되는 저장소에서는 왕복마다 비용도 든다
# (잡 하나가 최소 1분으로 계산된다).
# 아래 `drift`는 `make openapi`만 돌고 `make contracts`가 안 돌아 TS 계약이 어긋나는 경우를 잡는다.
#
# CI의 `lint` 잡과 `api-gateway` 잡을 명령까지 그대로 실행한다.
# 여기를 고치면 `.github/workflows/ci.yml`도 같이 고친다. 둘이 다르면 이 파일의 결과를 믿을 수 없다.
#
# CI는 스텝 하나가 실패하면 거기서 멈추지만, 이 스크립트는 끝까지 실행한 뒤 실패를 모아 알린다.
# 실패를 한 번에 모두 보면 하나씩 고쳐 다시 돌리는 왕복이 줄어든다.
set -uo pipefail
cd "$(dirname "$0")/../.."

FULL=0
[ "${1:-}" = "--all" ] && FULL=1

#: pytest가 쓸 전용 DB. conftest의 기본 테스트 DB는 쓰지 않는다. conftest는 끝날 때 패턴 매칭으로
#: 행을 지우므로, 같은 DB에서 다른 테스트 실행이 돌고 있으면 그쪽 행까지 지운다.
VERIFY_DB="${VERIFY_DB:-rebornstudio_verify}"
#: SSL을 끈다. 호스트에서 docker 컨테이너의 postgres로 붙으면 SSL 협상이 실패한다.
export PGSSLMODE=disable

#: 별도 npm 캐시 위치. 호스트의 npm 캐시(`~/.npm`)에 root 소유 파일이 있으면 `npx`가 EACCES로 실패한다.
#: 캐시 소유자를 바꾸려면 관리자 권한이 필요하므로 별도 위치를 쓴다.
#: CI 러너에서는 생기지 않는 문제라 ci.yml에는 이 설정이 없다.
export npm_config_cache="${NPM_CACHE:-${TMPDIR:-/tmp}/reborn-npm-cache}"
mkdir -p "$npm_config_cache"

#: 워크스페이스에 설치된 웹 도구 위치. CI는 pnpm으로 설치하고 부르지만 로컬에는 pnpm이
#: PATH에 없을 수 있다. `npx`로 매번 받아 오면 느리고 위 캐시 문제가 다시 생긴다.
WEB_BIN="apps/web/node_modules/.bin"

PASS=(); FAIL=()
run() {                       # run <이름> <명령…>
  local name="$1"; shift
  printf "  %-34s" "$name"
  local out; out=$("$@" 2>&1); local rc=$?
  if [ $rc -eq 0 ]; then echo "OK"; PASS+=("$name")
  else echo "실패"; FAIL+=("$name"); printf '%s\n' "$out" | tail -18 | sed 's/^/      /'; fi
}

# ── 자기 점검: ci.yml이 부르는 게이트를 여기서도 모두 실행하는가 ──────────────────
#
# 성공하면 마지막 줄에 "CI가 볼 것을 여기서 다 봤습니다"를 출력한다.
# CI에 게이트를 더하면서 여기를 고치지 않으면 로컬은 통과하는데 CI만 실패한다.
# 그래서 실행 전에 ci.yml이 부르는 스크립트가 이 파일에도 있는지 확인한다.
#
# 주석 줄은 세지 않는다. 설명에 게이트 이름이 적혀 있으면 그 낱말로 통과하기 때문이다.
#
# 게이트 이름에 숫자가 들 수 있다(`check-a11y-names`). `[a-z-]+`로 뽑으면 `check-a`에서 끊긴다.
#
# 루트 게이트는 `scripts/ci/`처럼 하위 폴더에 있다. 정규식이 `scripts/x.py` 모양만 받으면
# 게이트를 하나도 뽑지 못하고 반복문이 한 번도 돌지 않아 통과로 끝난다. 그래서 뽑은 수가
# ci.yml `run:` 줄의 `check-*` 수보다 적으면 점검 자체를 실패로 처리한다.
missing=""
runnable=$(grep -v '^[[:space:]]*#' scripts/ci/verify.sh)
gates=$(grep -oE "scripts/([a-z0-9_-]+/)*[a-z0-9-]+\.(py|mjs)" .github/workflows/ci.yml | sed 's#.*/##' | sort -u)
ci_checks=$(grep -E "^[[:space:]]+run: " .github/workflows/ci.yml | grep -oE "check-[a-z0-9-]+\.(py|mjs)" | sort -u)
if [ "$(printf '%s\n' "$gates" | grep -c '^check-')" -lt "$(printf '%s\n' "$ci_checks" | grep -c .)" ]; then
  echo "✗ 자기 점검이 ci.yml의 게이트 경로를 다 못 읽었습니다 — 위 정규식을 스크립트 위치에 맞춰 주세요."
  exit 1
fi
for f in $gates; do
  printf '%s' "$runnable" | grep -q "${f%.*}" || missing="$missing $f"
done
if [ -n "$missing" ]; then
  echo "✗ CI는 도는데 이 스크립트가 안 도는 게이트:$missing"
  echo "  → 위 목록에 더하고 다시 돌려주세요. 안 그러면 로컬 초록이 CI 초록을 뜻하지 않습니다."
  exit 1
fi

# ── 반대 방향: 여기선 도는데 CI엔 없는 게이트 ─────────────────────────────
#
# 여기에만 있고 ci.yml에는 없는 게이트도 찾는다. 로컬에서만 도는 게이트가 잡는 문제는
# CI가 검사하지 않으므로 그대로 머지될 수 있다.
#
# 일부러 CI 밖에 둔 것은 이유와 함께 여기 적는다. 이유 없이 빼면 다음 사람이
# 지워도 되는지 알 수 없다.
#   check-orphan-routes  붙일지 제거할지를 사람이 정한다.
#   check-utilities      `dist`가 있어야 돈다. PR 전에 수동으로 돌린다.
#   check-doc-prose      문서 산문은 사람이 쓰는 글이라 세어서 보여 주기만 한다.
ci_exempt="check-orphan-routes check-utilities check-doc-prose"
extra=""
for f in $(printf '%s' "$runnable" | grep -oE "check-[a-z0-9-]+" | sort -u); do
  case " $ci_exempt " in *" $f "*) continue ;; esac
  grep -E "^[[:space:]]+run: " .github/workflows/ci.yml | grep -q "$f" || extra="$extra $f"
done
if [ -n "$extra" ]; then
  echo "✗ 여기선 도는데 CI엔 없는 게이트:$extra"
  echo "  → ci.yml에 더하거나, 일부러 뺀 것이면 위 ci_exempt 에 **이유와 함께** 적어주세요."
  exit 1
fi

echo "── 파이썬 (CI: lint) ──"
run "ruff"              uvx ruff check .
run "import-linter"     bash -c 'cd apps/api-gateway && uv run --group test lint-imports'
run "import-linter (workspace)" uv run --all-packages --group test lint-imports --config .importlinter
for g in check-dead-docstrings check-error-codes check-event-labels check-jsonb-merge check-env-catalog check-retry-caps; do
  run "$g"              python3 "scripts/ci/$g.py"
done

echo "── 웹 (CI: lint) ──"
if command -v pnpm >/dev/null; then
  run "pnpm install"    pnpm install --frozen-lockfile
else
  echo "  pnpm install                      건너뜀 (PATH에 pnpm 없음 — 설치본을 그대로 씁니다)"
fi
run "tsc --noEmit"      bash -c "cd apps/web && ../../$WEB_BIN/tsc --noEmit"
# 위 자기 점검은 ci.yml의 `scripts/**/*.{py,mjs}`만 센다. vitest는 스크립트가 아니라 잡히지 않는다.
# ci.yml의 `vitest (web 순수 로직)` 스텝과 같이 고친다.
run "vitest (web)"      bash -c "cd apps/web && ../../$WEB_BIN/vitest run"
for g in check-a11y-names check-headings check-query-states check-danger-copy check-jargon; do
  [ -f "apps/web/scripts/$g.mjs" ] && run "$g" node "apps/web/scripts/$g.mjs" --strict
done
run "check-false-signals" node apps/web/scripts/check-false-signals.mjs
run "check-surface"     node apps/web/scripts/check-surface.mjs
run "check-orphan-docstrings" node apps/web/scripts/check-orphan-docstrings.mjs
run "check-missing-routes" node scripts/ci/check-missing-routes.mjs
run "check-request-bodies" node scripts/ci/check-request-bodies.mjs
run "check-copy"        bash -c 'cd apps/web && node scripts/check-copy.mjs'
# 규칙 하나라도 1건이 되면 막는다. 위치는 --list 로 본다.
run "check-feature-boundaries" node apps/web/scripts/check-feature-boundaries.mjs --strict
# 막지 않는다. 문장 잇기와 삽입구를 기계가 항상 정확히 구분하지는 못한다.
# 수가 늘었으면 --list 로 위치를 보고 사람이 정한다.
run "check-doc-prose"   python3 scripts/ci/check-doc-prose.py
# 주석 말투. 오검출이 거의 없는 네 규칙은 1건이라도 있으면 막는다.
# 나머지 규칙은 문맥을 봐야 판정할 수 있어 아래 보고 모드로 세기만 한다. 위치는 --list 로 본다.
run "check-comment-style" python3 scripts/ci/check-comment-style.py --strict \
  --rule 지시어 --rule 강조기호 --rule 대시잇기 --rule 1인칭
python3 scripts/ci/check-comment-style.py || true

echo "── 계약 drift (CI: api-gateway) ──"
run "openapi drift"     bash -c '
  uv run --package api-gateway python scripts/dev/gen_openapi.py >/dev/null &&
  git diff --exit-code docs/api/openapi.json'
run "contracts drift"   bash -c '
  npx --yes openapi-typescript docs/api/openapi.json \
    -o packages/reborn-contracts/typescript/src/api.ts >/dev/null &&
  git diff --exit-code packages/reborn-contracts/typescript/src/api.ts'

if [ $FULL -eq 1 ]; then
  echo "── pytest (CI: api-gateway) — private DB $VERIFY_DB ──"
  # DB가 필요 없어 postgres 확인 전에 돈다.
  run "pytest (reborn-transcode)" bash -c 'cd packages/reborn-transcode && uv run --package reborn-transcode --group test pytest tests/ -q'
  run "pytest (worker-jobs)"      bash -c 'cd workers/worker-jobs && uv run --package worker-jobs --group test pytest tests/ -q'
  PG=$(docker ps -q -f name=postgres | head -1)
  if [ -z "$PG" ]; then
    echo "  postgres 컨테이너가 없습니다 — 'make dev-up' 후 다시 돌려주세요"; FAIL+=("pytest(준비)")
  else
    docker exec "$PG" psql -U reborn -d postgres \
      -c "CREATE DATABASE $VERIFY_DB" >/dev/null 2>&1 || true
    export DATABASE_URL="postgresql+asyncpg://reborn:reborn@127.0.0.1:5433/$VERIFY_DB"
    run "alembic upgrade head" bash -c 'cd apps/api-gateway && uv run --package api-gateway alembic upgrade head'
    # CI와 같은 병렬도로 돈다. 로컬은 통과하는데 CI만 실패하는 경우를 줄이려면
    # 워커 수까지 같아야 한다(워커별 DB는 `tests/_xdist_split.py`가 만든다).
    run "pytest (-n 4)"        bash -c 'cd apps/api-gateway && uv run --package api-gateway --group test pytest tests/ -q -n 4'
    # ci.yml과 같은 순서로 api-gateway 뒤에 돈다. 같은 private DB를 쓴다.
    run "pytest (worker-io)"   bash -c 'cd workers/worker-io && uv run --package worker-io --group test pytest tests/ -q'
  fi
fi

echo
if [ ${#FAIL[@]} -eq 0 ]; then
  echo "✅ ${#PASS[@]}개 통과 — CI가 볼 것을 여기서 다 봤습니다."
  [ $FULL -eq 0 ] && echo "   (pytest는 안 돌았습니다 — 'make verify-all')"
  exit 0
fi
echo "❌ ${#FAIL[@]}개 실패: ${FAIL[*]}"
echo "   고치고 다시 돌리세요 — 여기서 잡으면 CI 실행 한 번(평균 4.5분)을 안 씁니다."
exit 1
