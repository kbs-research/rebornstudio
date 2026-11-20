#!/usr/bin/env bash
# Postgres를 논리 백업해 NCP Object Storage에 올린다.
#
# DB가 운영 서버 안의 컨테이너라서 관리형 DB의 자동 백업·PITR이 없다.
# 라벨링 결과 전체가 서버 디스크 하나에 있으므로 이 스크립트가 백업을 맡는다.
#
# 논리 백업(pg_dump)을 쓰는 이유: 이 DB는 메타데이터만 담는다. 영상·클립 파일은 모두
# 오브젝트 스토리지에 있다. 그래서 덤프가 작고, 버전이 달라도 복원되며, 한 테이블만
# 꺼낼 수도 있다. PITR이 필요할 만큼 커지면 NCP Cloud DB로 옮긴다.
# 여기서 WAL 아카이빙을 흉내 내지 않는다.
#
# 성공했을 때만 하트비트를 남긴다. 백업이 멈추면 진단의 DB 백업 항목이 마지막 성공 시각으로
# 그 사실을 보인다(주기 12h · 실패 판정 25h라 두 번 연속 놓쳐야 실패로 바뀐다).
set -euo pipefail

: "${PGPASSWORD:?}"
: "${BACKUP_BUCKET:?백업을 올릴 NCP 버킷 필요}"
# 키는 compose가 BACKUP_ACCESS_KEY·BACKUP_SECRET_KEY로 넘긴다. 앱은 키를 자격증명 연결(DB)에서
# 읽지만 백업은 DB 없이 돌므로 env 키가 있어야 한다.
: "${AWS_ACCESS_KEY_ID:?백업 버킷 키 필요 — BACKUP_ACCESS_KEY}"
: "${AWS_SECRET_ACCESS_KEY:?백업 버킷 키 필요 — BACKUP_SECRET_KEY}"
PGHOST="${PGHOST:-postgres}"
PGUSER="${PGUSER:-reborn}"
PGDATABASE="${PGDATABASE:-rebornstudio}"
S3_ENDPOINT="${S3_ENDPOINT:-https://kr.object.ncloudstorage.com}"
BACKUP_PREFIX="${BACKUP_PREFIX:-backups/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
REDIS_URL_HOST="${REDIS_HOST:-redis}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
key="$BACKUP_PREFIX/rebornstudio-$stamp.dump.gz"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "▶ pg_dump $PGDATABASE"
# -Fc(custom) 형식. pg_restore가 병렬 복원·부분 복원을 할 수 있다. 평문 SQL은 통째로만 된다.
pg_dump -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -Fc \
  | gzip -9 > "$tmp/dump.gz"

size=$(wc -c < "$tmp/dump.gz")
# 1KiB 이하 덤프는 올리지 않고 실패로 끝낸다. 빈 백업을 성공으로 기록하면 복원할 때가 되어서야
# 백업이 없다는 사실을 알게 된다.
[ "$size" -gt 1024 ] || { echo "❌ 덤프가 너무 작다($size B) — 올리지 않는다"; exit 1; }
echo "  크기 $(( size / 1024 )) KiB"

echo "▶ 업로드 s3://$BACKUP_BUCKET/$key"
aws --endpoint-url "$S3_ENDPOINT" s3 cp "$tmp/dump.gz" "s3://$BACKUP_BUCKET/$key" --only-show-errors

echo "▶ 보관 정리 (${RETENTION_DAYS}일)"
# epoch 산술로 자른다. `date -d "30 days ago"`는 GNU 전용이라 이 이미지의 busybox date에서는
# 업로드까지 성공한 뒤 정리 단계에서 실패한다. `-d @초`는 양쪽 모두 지원한다.
cutoff=$(date -u -d "@$(( $(date -u +%s) - RETENTION_DAYS * 86400 ))" +%Y%m%d)
aws --endpoint-url "$S3_ENDPOINT" s3 ls "s3://$BACKUP_BUCKET/$BACKUP_PREFIX/" \
  | awk '{print $4}' | while read -r name; do
  [ -n "$name" ] || continue
  # 파일 이름의 날짜로 자른다. 스토리지의 LastModified는 재업로드·복사로 바뀔 수 있다.
  d="${name#rebornstudio-}"; d="${d%%T*}"
  case "$d" in ''|*[!0-9]*) continue ;; esac
  if [ "$d" -lt "$cutoff" ]; then
    echo "  삭제 $name"
    aws --endpoint-url "$S3_ENDPOINT" s3 rm "s3://$BACKUP_BUCKET/$BACKUP_PREFIX/$name" --only-show-errors
  fi
done

# 하트비트. 키 이름은 진단(`api_gateway.services.ops.diagnostics`)의 DB 백업 항목과 같아야 한다.
redis-cli -h "$REDIS_URL_HOST" set "reborn:beat:pg-backup" "$(date +%s)" >/dev/null || \
  echo "⚠ 하트비트 기록 실패 — 백업 자체는 성공했다"

echo "✅ 백업 완료: $key"
