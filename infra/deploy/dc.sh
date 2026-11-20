#!/usr/bin/env bash
# 서버에서 compose를 손으로 부를 때 쓰는 래퍼.
#
# `docker compose ps` 한 번에도 인자가 넷 필요하다. 프로젝트 이름·compose 파일·env 파일·`IMAGE_TAG`다.
# 앞의 셋은 외우면 되지만 IMAGE_TAG는 `.env`에 없다(배포 스크립트가 환경으로 넘긴다).
# 그래서 그냥 부르면 이렇게 된다:
#
#     error while interpolating services.api.image: required variable IMAGE_TAG is missing a value
#
# 장애를 확인하는 중에 이 오류가 나면 원인 대신 명령 인자를 찾느라 시간을 쓴다.
# 마지막으로 배포된 태그는 `.last-deploy`에 있으니 여기서 읽어 채운다.
#
#   dc.sh ps
#   dc.sh logs -f api
#   dc.sh restart worker-gpu
#   dc.sh exec -T api curl -fsS http://localhost:8000/healthz
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/rebornstudio}"
# 스크립트 위치는 cd 전에 잡는다. 상대 경로로 불렸으면 cd 뒤에는 찾지 못한다.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

[ -f .env ] || { echo "❌ $APP_DIR/.env 없음"; exit 1; }

# 배포된 적이 없으면 태그를 지어내지 않는다. 임의의 태그로 `up`을 실행하면 그 태그가 배포된다.
IMAGE_TAG="${IMAGE_TAG:-$(cat .last-deploy 2>/dev/null || true)}"
[ -n "$IMAGE_TAG" ] || {
  echo "❌ .last-deploy가 없습니다 — 아직 성공한 배포가 없다는 뜻입니다."
  echo "   특정 태그로 보려면: IMAGE_TAG=sha-... $0 $*"
  exit 1
}
export IMAGE_TAG

# 프로필은 배포와 같은 규칙(profiles.sh)으로 `.env`가 정한다. 규칙이 다르면 `ps`가 배포된
# 것과 다른 목록을 보여 준다(배포가 띄운 pg-backup이 보이지 않는 식이다).
# shellcheck source=profiles.sh
. "$SCRIPT_DIR/profiles.sh"

# shellcheck disable=SC2046  # 프로필 인자는 단어로 쪼개져야 한다
# GPU 덮어쓰기도 배포와 같은 규칙(`compose_files`)으로 얹는다. 빠지면 `ps`·`restart`가 CPU 이미지로 본다.
exec docker compose -p rebornstudio-essential \
  -f infra/compose/compose.yaml -f infra/compose/compose.prod.yaml $(compose_files .env .) \
  --env-file .env $(compose_profiles .env) "$@"
