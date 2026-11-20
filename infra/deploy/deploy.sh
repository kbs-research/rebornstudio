#!/usr/bin/env bash
# 서버에서 도는 배포 스크립트. CI가 rsync로 올린 뒤 ssh로 이 스크립트를 부른다.
#
# 순서는 pull → 백업 → migrate → up → 헬스 확인 → prune이다.
# 서버에서 빌드한 이미지로 배포하면(`DEPLOY_PULL=false`) pull 대신 로컬 이미지가 모두 있는지 확인한다.
#
#   - pull이 먼저다. 이미지를 당기지 못하면 아무것도 바뀌지 않은 채로 끝난다.
#     migrate부터 하면 스키마만 앞서 나가고 앱은 구버전인 상태로 남는다.
#   - 적용할 마이그레이션이 있으면 바로 앞에서 DB를 백업한다. 백업이 실패하면 멈춘다.
#     마이그레이션은 되돌리지 않으므로 잘못된 마이그레이션에서 돌아갈 곳은 이 백업뿐이다.
#   - migrate가 up보다 먼저다. 마이그레이션은 언제나 구 코드와 호환이어야 하므로
#     앱보다 먼저 돌아도 안전하다.
#   - DB의 리비전을 이미지가 모르면 마이그레이션을 건너뛴다. 새 마이그레이션이 들어간 배포를
#     직전 태그로 되돌리는 롤백이 그 경우다. 직전 이미지에는 새 리비전 파일이 없어
#     `alembic upgrade head`가 실패한다. `SKIP_MIGRATE=1`이면 판단 없이 건너뛴다.
#   - prune이 마지막이다. 헬스가 깨지면 직전 이미지가 아직 로컬에 있어야 바로 롤백할 수 있다.
#     성공을 확인한 뒤에만 지우고, 지금 태그와 직전 태그의 이미지는 남긴다.
#
# 이 스크립트는 `.env`를 만들지도 고치지도 않는다. 시크릿은 서버에만 있고 CI에는 넘기지 않는다.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/rebornstudio}"
# 공통 형상 위에 운영 덮어쓰기를 얹는다. 뒤 파일이 앞 파일을 덮으므로 순서를 바꾸지 않는다.
COMPOSE_FILES=(-f "$APP_DIR/infra/compose/compose.yaml" -f "$APP_DIR/infra/compose/compose.prod.yaml")
ENV_FILE="$APP_DIR/.env"
PROJECT="rebornstudio-essential"
LAST_DEPLOY="$APP_DIR/.last-deploy"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
# 서버에서 직접 빌드한 이미지(build-images.sh)로 배포하면 false로 준다. 레지스트리에서 당기지 않는다.
DEPLOY_PULL="${DEPLOY_PULL:-true}"
# 1이면 백업과 마이그레이션을 건너뛴다. 리비전 판단이 맞지 않을 때 사람이 고르는 탈출구다.
SKIP_MIGRATE="${SKIP_MIGRATE:-0}"

: "${IMAGE_TAG:?IMAGE_TAG 필요 (예: sha-abc123...)}"

# 켤 프로필과 덮어쓰기 파일은 `.env`가 정한다. 규칙은 profiles.sh 한 곳에 있고 dc.sh·Makefile도
# 같은 규칙을 쓴다. 백업과 비식별화 워커는 프로필 없이 늘 뜬다.
# shellcheck source=profiles.sh
. "$(cd "$(dirname "$0")" && pwd)/profiles.sh"

compose() {
  # shellcheck disable=SC2046  # 프로필 인자는 단어로 쪼개져야 한다
  docker compose -p "$PROJECT" "${COMPOSE_FILES[@]}" $(compose_files "$ENV_FILE" "$APP_DIR") \
    --env-file "$ENV_FILE" $(compose_profiles "$ENV_FILE") "$@"
}

# .env는 서버에만 있다. 없으면 여기서 멈춘다. `${VAR:?}`가 up에서 잡아 주지만
# 그때는 이미 migrate가 돈 뒤다.
[ -f "$ENV_FILE" ] || { echo "❌ $ENV_FILE 없음 — 서버 부트스트랩을 먼저 하세요"; exit 1; }

# 백업은 늘 켠다. 값이 비면 compose의 `:?`도 막지만 그 오류는 처음 빠진 변수 하나만 알린다.
# 빠진 키를 pull 전에 한 번에 모아 알린다.
missing=""
for key in BACKUP_BUCKET BACKUP_ACCESS_KEY BACKUP_SECRET_KEY; do
  grep -qE "^${key}=.+" "$ENV_FILE" || missing="$missing $key"
done
if [ -n "$missing" ]; then
  echo "❌ 백업 설정이 비어 있습니다:$missing"
  echo "   $ENV_FILE에 백업 버킷과 그 버킷 전용 키를 채우고 다시 배포하세요"
  exit 1
fi

PREV_TAG="$(cat "$LAST_DEPLOY" 2>/dev/null || echo '(없음)')"
echo "▶ 배포: $PREV_TAG → $IMAGE_TAG"
BACKUP_BUCKET_NAME="$(sed -n 's/^BACKUP_BUCKET=//p' "$ENV_FILE" | tail -n 1)"
echo "  백업: 12시간마다 · 30일 보관 · 버킷 $BACKUP_BUCKET_NAME"
if grep -qE "^GPU_ENABLED=true$" "$ENV_FILE"; then
  echo "  GPU: 켜짐 (비식별화 추론은 CUDA, 인코딩은 NVENC로 처리합니다)"
else
  echo "  GPU: 꺼짐 (비식별화 추론과 인코딩을 CPU로 처리합니다)"
fi
# 비식별화 워커는 늘 뜬다. 모델은 워커 안에서 돌고 `.env`의 DEIDENT_MODEL이 비면 기본 모델을 쓴다.
DEIDENT_MODEL_NAME="$(sed -n 's/^DEIDENT_MODEL=//p' "$ENV_FILE" 2>/dev/null | tail -n 1)"
echo "  비식별화 워커: 켜짐 · 모델 ${DEIDENT_MODEL_NAME:-essential-yunet}"

export IMAGE_TAG
REGISTRY="$(sed -n 's/^IMAGE_REGISTRY=//p' "$ENV_FILE" | tail -n 1 | tr -d '\r"')"

if [ "$DEPLOY_PULL" = "false" ]; then
  # 앱 이미지가 하나라도 없으면 up이 레지스트리에서 당기려다 실패한다. 마이그레이션 전에 확인한다.
  # postgres·redis 같은 공개 이미지는 Docker Hub에서 당긴다.
  echo "▶ 1/5 로컬 이미지 확인"
  for image in $(compose config --images | sort -u); do
    docker image inspect "$image" >/dev/null 2>&1 && continue
    case "$image" in
      "$REGISTRY"/rebornstudio-*)
        echo "❌ 로컬에 이미지가 없습니다: $image"
        echo "   먼저 빌드하세요: $APP_DIR/infra/deploy/build-images.sh $IMAGE_TAG"
        exit 1 ;;
      *) docker pull --quiet "$image" >/dev/null ;;
    esac
  done
else
  echo "▶ 1/5 이미지 당기기"
  compose pull --quiet
fi

# DB의 마이그레이션 리비전을 출력한다. 장부 테이블이 없으면(새 설치) 빈 문자열이다.
db_revision() {
  local q="select version_num from alembic_version limit 1"
  if [ "$(compose exec -T postgres psql -U reborn -d rebornstudio -tAc \
          "select to_regclass('public.alembic_version') is not null")" = "t" ]; then
    compose exec -T postgres psql -U reborn -d rebornstudio -tAc "$q"
  fi
}

# 이 이미지의 마이그레이션 명령을 실행한다. migrate 서비스와 같은 이미지·환경이다.
alembic_cmd() { compose --profile ops run --rm -T migrate uv run --package api-gateway alembic "$@"; }

echo "▶ 2/5 마이그레이션"
if [ "$SKIP_MIGRATE" = "1" ]; then
  echo "  SKIP_MIGRATE=1 — 백업과 마이그레이션을 건너뜁니다"
else
  compose up -d --wait postgres >/dev/null
  DB_REV="$(db_revision | tr -d '[:space:]')"
  heads_out="$(alembic_cmd heads 2>&1)" || true
  IMAGE_HEAD="$(printf '%s\n' "$heads_out" | awk '$2 == "(head)" {print $1; exit}')"
  if [ -z "$IMAGE_HEAD" ]; then
    echo "❌ 이미지의 마이그레이션 head를 읽지 못했습니다:"; printf '%s\n' "$heads_out" | tail -5 | sed 's/^/   /'
    exit 1
  fi
  if [ -z "$DB_REV" ]; then
    # 새 설치. 지킬 데이터가 없으므로 백업하지 않는다(빈 덤프는 backup.sh가 실패로 다룬다).
    echo "  새 설치 — 스키마를 $IMAGE_HEAD까지 만듭니다"
    compose --profile ops run --rm migrate
  elif [ "$DB_REV" = "$IMAGE_HEAD" ]; then
    echo "  적용할 마이그레이션 없음 (리비전 $DB_REV)"
  else
    show_rc=0
    show_out="$(alembic_cmd show "$DB_REV" 2>&1)" || show_rc=$?
    if [ "$show_rc" -ne 0 ] && printf '%s' "$show_out" | grep -q "Can't locate revision"; then
      echo "  DB 리비전 $DB_REV가 이 이미지(head $IMAGE_HEAD)에 없습니다."
      echo "  더 새 버전이 올린 스키마로 보고 마이그레이션을 건너뜁니다(롤백 배포)."
      echo "  새 스키마는 이전 코드와 호환되어야 합니다. 컬럼을 지운 버전은 되돌리지 않고 수정 버전을 배포합니다."
    elif [ "$show_rc" -ne 0 ]; then
      echo "❌ 리비전 $DB_REV를 확인하지 못했습니다:"; printf '%s\n' "$show_out" | tail -5 | sed 's/^/   /'
      exit 1
    else
      echo "  $DB_REV → $IMAGE_HEAD. 적용 전에 DB를 백업합니다"
      compose run --rm -T pg-backup backup.sh || {
        echo "❌ 백업 실패 — 마이그레이션을 시작하지 않았습니다. 아무것도 바뀌지 않았습니다"
        echo "   백업 버킷과 키를 확인하고 다시 배포하세요"
        exit 1
      }
      compose --profile ops run --rm migrate
    fi
  fi
fi

echo "▶ 3/5 기동"
compose up -d --remove-orphans

echo "▶ 4/5 헬스 확인 (최대 ${HEALTH_TIMEOUT}s)"

# web 컨테이너 안에서 확인한다. 호스트에서 :80으로 요청하면 앞단 LB·방화벽까지
# 같이 검사하게 되어, 앱이 정상인데 배포가 실패로 보일 수 있다.
# localhost는 ::1로 풀릴 수 있는데 web nginx는 IPv4만 연다. 그래서 주소를 적는다.
probe_web() { compose exec -T web wget -qO- http://127.0.0.1/healthz 2>&1; }
# readiness를 본다. 프로세스가 떠 있어도 DB나 Redis에 붙지 못하면 요청은 실패한다.
probe_api() { compose exec -T api curl -fsS http://localhost:8000/readyz 2>&1; }

# 모든 서비스가 돌고 있고, 헬스체크가 있는 서비스는 healthy인지 본다. 아니면 그 목록을 출력한다.
# 워커는 HTTP 프로브가 없으므로 컨테이너 상태와 헬스체크로 본다. 재시작을 되풀이하는
# 워커는 web·api가 멀쩡해도 작업을 처리하지 못한다.
services_not_ready() {
  local svc cid st
  for svc in $(compose config --services); do
    cid="$(compose ps -q "$svc" 2>/dev/null || true)"
    if [ -z "$cid" ]; then echo "$svc(컨테이너 없음)"; continue; fi
    st="$(docker inspect -f '{{.State.Status}}{{if .State.Health}}/{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null || echo "알 수 없음")"
    case "$st" in running|running/healthy) ;; *) echo "$svc($st)" ;; esac
  done
}

# 실패했을 때 무엇이 왜 실패했는지 알린다.
#
# 프로브 출력을 버리면 web이 실패했는지, api가 실패했는지, `exec` 자체가 실패했는지
# (컨테이너가 뜨자마자 종료되면 그렇다) 구분할 수 없고 "헬스 실패" 한 줄만 남는다.
#
# 로그 본문은 찍지 않는다. 첫 부팅의 셋업 토큰이 api 로그에 있고(이 파일 아래쪽 안내가
# 그 위치를 가리킨다), CI 로그로 흘리면 되돌릴 수 없다. 대신 어느 쪽이 어떻게 실패했는지와
# 컨테이너 상태만 보이고, 로그는 사람이 서버에서 열게 한다.
health_report() {
  # `set -e` 아래에서는 `w="$(probe_web)"` 한 줄이 그 자리에서 스크립트를 끝낸다.
  # 보고 싶은 것이 바로 프로브 실패이므로 `|| rc=$?`로 받는다.
  local w a wrc=0 arc=0
  w="$(probe_web)" || wrc=$?
  a="$(probe_api)" || arc=$?
  # 아래 값은 실패를 확정한 뒤 한 번 더 요청한 결과다. 그때 응답하면 `exit=0`이 찍힌다.
  # `exit=0`은 기동이 상한보다 느리다는 신호라서 그렇다고 알린다.
  echo "   (아래는 상한을 넘긴 뒤 한 번 더 두드린 결과입니다)"
  echo "   web  프로브 exit=$wrc  ${w:-(출력 없음)}"
  echo "   api  프로브 exit=$arc  ${a:-(출력 없음)}"
  local nr
  nr="$(services_not_ready | tr '\n' ' ')"
  echo "   준비되지 않은 서비스: ${nr:-(없음)}"
  # 설정 검증 실패는 문구만 골라 보인다. 로그 본문은 여전히 찍지 않는다(셋업 토큰이 거기 있다).
  # `grep -o`라 찍히는 것은 매치된 문구뿐이고, 그 패턴은 config.py의 prod validator가 내는
  # 문장뿐이라 토큰이 섞일 수 없다.
  #
  # 이 출력이 없으면 시크릿이 있는데 잘못된 경우(32자 미만·`dev-` 접두)가 compose의 `:?`를
  # 통과해 컨테이너만 조용히 재시작 루프에 들고, 배포 로그에는 이유가 남지 않는다.
  local cfg=""
  cfg="$(compose logs --tail=200 api 2>&1 | grep -oE \
        '[a-z_]+ must (be set \(not a default\)|be >=32 chars|not contain localhost)[a-z_ -]*' \
        | sort -u | head -3)" || true
  if [ -n "$cfg" ]; then
    echo "   설정 검증 실패 — 서버 env를 고치고 다시 배포하세요:"
    printf '%s\n' "$cfg" | sed 's/^/     /'
  fi
  echo "   컨테이너 상태:"
  # `|| true` 가 없으면 `ps` 가 실패할 때(도커가 응답 없음 등) pipefail+errexit 로 여기서
  # 끝나 아래 롤백 안내가 찍히지 않는다. 진단하려다 복구 수단을 가리게 된다.
  compose ps 2>&1 | sed 's/^/     /' || true
}

deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
while :; do
  if probe_web >/dev/null 2>&1 && probe_api >/dev/null 2>&1 \
     && [ -z "$(services_not_ready)" ]; then
    echo "✅ 헬스 OK"
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "❌ 헬스 실패 — 배포된 태그는 $IMAGE_TAG 입니다."
    # 마지막 시도를 한 번 더 돌려 그 결과를 보인다. 루프 안에서 매번 찍으면
    # 기동 중의 정상적인 실패가 수십 줄 쌓여 실제 신호를 덮는다.
    health_report
    echo "   로그:   docker compose -p $PROJECT ${COMPOSE_FILES[*]} logs --tail=100 api web"
    echo "   롤백:   IMAGE_TAG=$PREV_TAG DEPLOY_PULL=$DEPLOY_PULL $APP_DIR/infra/deploy/deploy.sh"
    echo "   (직전 이미지는 아직 로컬에 있습니다 — prune 전에 멈췄습니다)"
    exit 1
  fi
  sleep 5
done

echo "$IMAGE_TAG" > "$LAST_DEPLOY"

echo "▶ 5/5 정리"
# 디스크가 차면 Redis AOF 쓰기가 실패해 워커가 멈춘다. 배포마다 이미지 한 세트(수 GB)가 쌓이므로
# 지금 태그와 직전 태그(롤백 대상)를 뺀 앱 이미지를 지운다. 컨테이너가 쓰는 이미지는 docker가 지우지 않는다.
# `docker image prune -a`는 쓰지 않는다. 직전 태그도 지워 롤백할 이미지가 사라진다.
docker image ls --format '{{.Repository}}:{{.Tag}}' | while read -r ref; do
  case "$ref" in "$REGISTRY"/rebornstudio-*:*) ;; *) continue ;; esac
  tag="${ref##*:}"
  if [ "$tag" = "$IMAGE_TAG" ] || [ "$tag" = "$PREV_TAG" ] || [ "$tag" = "<none>" ]; then continue; fi
  if docker image rm "$ref" >/dev/null 2>&1; then echo "  지움 $ref"; fi
done
docker image prune -f >/dev/null
# 서버에서 빌드하면 빌드 캐시가 쌓인다. 일주일 넘게 쓰지 않은 캐시만 지워 다음 빌드 속도는 지킨다.
docker builder prune -f --filter until=168h >/dev/null 2>&1 || true

echo "✅ 배포 완료: $IMAGE_TAG"

# 최초 설정 안내. 배포한 사람이 로그를 뒤지지 않게 한다.
#
# 새 환경의 첫 배포는 계정이 하나도 없는 상태로 끝난다. 계정이 없는 상태가 정상이지만, 모르면
# "배포는 됐는데 로그인이 안 된다"가 된다. 첫 관리자를 만들려면 셋업 토큰이
# 필요한데 그 토큰은 api 로그에만 있다. 어디를 봐야 하는지까지 여기서 알린다.
#
# 토큰 값은 출력하지 않는다. 이 스크립트의 출력은 CD 로그에도 남고,
# 그 로그를 읽는 사람이 토큰으로 첫 super_admin을 만들 수 있다. 서버에서 로그를 보는 명령만 안내한다.
#
# 실패는 조용히 넘긴다. 안내를 못 만들었다고 배포를 실패로 만들 이유가 없다.
if compose exec -T api curl -fsS http://localhost:8000/setup/status 2>/dev/null \
     | grep -q '"uninitialized":true'; then
  echo
  echo "──────────────────────────────────────────────────────────────"
  echo "  이 배포에는 아직 관리자 계정이 없습니다 (정상입니다)."
  echo "  브라우저로 접속하면 최초 설정 화면으로 안내됩니다."
  echo
  echo "  셋업 토큰은 서버에서 api 로그로 확인합니다:"
  echo "    $APP_DIR/infra/deploy/dc.sh logs api | grep '셋업 토큰'"
  echo "──────────────────────────────────────────────────────────────"
fi
