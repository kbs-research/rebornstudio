#!/usr/bin/env bash
# 서버에서 저장소 소스로 운영 이미지를 빌드한다. 레지스트리 없이 서버 한 대에 설치할 때 쓴다.
#
# 빌드한 이미지는 서버 `.env`의 IMAGE_REGISTRY 이름으로 태그한다. compose가 그 이름으로 이미지를 찾으므로
# 레지스트리에 올리지 않아도 `DEPLOY_PULL=false deploy.sh`가 로컬 이미지로 기동한다.
# 레지스트리를 쓰지 않으면 IMAGE_REGISTRY에는 아무 이름(예: local)이나 넣는다.
#
# `.env`의 GPU_ENABLED가 true이면 worker-gpu 이미지를, 아니면 worker-jobs 이미지를 빌드한다.
# 서버는 둘 중 하나만 쓰고, worker-gpu 이미지는 CUDA 런타임을 담아 수 GB라서 쓰지 않는 쪽은 빌드하지 않는다.
#
# 쓰는 법(저장소 루트 /opt/rebornstudio에서):
#   infra/deploy/build-images.sh                 # 태그는 sha-<지금 커밋의 전체 sha>
#   infra/deploy/build-images.sh sha-<전체 sha>  # 태그를 직접 준다
# 끝나면 배포 명령을 출력한다.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/rebornstudio}"
cd "$APP_DIR"
ENV_FILE="$APP_DIR/.env"
[ -f "$ENV_FILE" ] || { echo "❌ $ENV_FILE 없음 — infra/deploy/env.example을 복사해 먼저 채우세요"; exit 1; }

REGISTRY="$(sed -n 's/^IMAGE_REGISTRY=//p' "$ENV_FILE" | tail -n 1 | tr -d '\r"')"
[ -n "$REGISTRY" ] || { echo "❌ .env의 IMAGE_REGISTRY가 비어 있습니다 (레지스트리를 쓰지 않으면 local)"; exit 1; }

# 태그는 커밋에서 만든다. 커밋하지 않은 변경이 있으면 같은 태그가 다른 내용을 가리키게 되어 롤백이 어긋난다.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "❌ 커밋하지 않은 변경이 있습니다. 이미지 태그가 커밋을 가리키지 못합니다"
  git status --short --untracked-files=no
  exit 1
fi
TAG="${1:-sha-$(git rev-parse HEAD)}"

IMAGES=(api web worker-io pgbackup)
if grep -qE "^GPU_ENABLED=true$" "$ENV_FILE"; then
  IMAGES+=(worker-gpu)
else
  IMAGES+=(worker-jobs)
fi

echo "▶ 이미지 빌드: $REGISTRY/rebornstudio-*:$TAG"
for n in "${IMAGES[@]}"; do
  echo "  · $n"
  docker build -q -f "infra/docker/$n.Dockerfile" -t "$REGISTRY/rebornstudio-$n:$TAG" . >/dev/null
done

echo "✅ 빌드 완료: ${IMAGES[*]}"
echo "   배포: IMAGE_TAG=$TAG DEPLOY_PULL=false $APP_DIR/infra/deploy/deploy.sh"
