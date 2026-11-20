#!/usr/bin/env bash
# 백업을 복원한다. 복원해 본 적 없는 백업은 믿을 수 없으므로 검증 복원을 기본으로 한다.
#
# 기본 동작은 별도 DB(rebornstudio_restore_check)에 부어 보고 행 수를 세는 검증 복원이다.
# 운영 DB를 덮어쓰려면 `--into-production`을 명시해야 한다. 위험한 일이 실수로 일어나지 않게 한다.
#
#   목록:   restore.sh --list
#   검증:   restore.sh <키 또는 latest>
#   실제:   restore.sh <키> --into-production
#
# 실제 복원은 public 스키마를 지우고 다시 만든 뒤 덤프를 붓는다. 백업 뒤에 생긴 테이블이나 컬럼이
# 남으면 마이그레이션 장부와 실제 스키마가 어긋난다. 지우기와 붓기는 한 트랜잭션이라
# 중간에 오류가 나면 원래 DB가 그대로 남는다. 앱이 DB에 붙어 있으면 시작하지 않는다.
# 복원한 뒤에는 마이그레이션을 실행하고 앱을 올린다(infra/backup/README.md의 "복원 절차").
set -euo pipefail

: "${PGPASSWORD:?}"
: "${BACKUP_BUCKET:?}"
PGHOST="${PGHOST:-postgres}"
PGUSER="${PGUSER:-reborn}"
PGDATABASE="${PGDATABASE:-rebornstudio}"
S3_ENDPOINT="${S3_ENDPOINT:-https://kr.object.ncloudstorage.com}"
BACKUP_PREFIX="${BACKUP_PREFIX:-backups/postgres}"
CHECK_DB="${CHECK_DB:-rebornstudio_restore_check}"

s3() { aws --endpoint-url "$S3_ENDPOINT" "$@"; }

if [ "${1:-}" = "--list" ]; then
  s3 s3 ls "s3://$BACKUP_BUCKET/$BACKUP_PREFIX/"
  exit 0
fi

target="${1:?복원할 키 또는 latest}"
if [ "$target" = "latest" ]; then
  target="$(s3 s3 ls "s3://$BACKUP_BUCKET/$BACKUP_PREFIX/" | awk '{print $4}' | sort | tail -1)"
  [ -n "$target" ] || { echo "❌ 백업이 없다"; exit 1; }
  echo "▶ latest = $target"
fi

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
s3 s3 cp "s3://$BACKUP_BUCKET/$BACKUP_PREFIX/$target" "$tmp/dump.gz" --only-show-errors
gunzip "$tmp/dump.gz"

if [ "${2:-}" = "--into-production" ]; then
  # 앱이나 워커가 붙어 있으면 복원 도중 옛 행을 쓰거나 스키마 삭제를 기다리며 막힌다.
  others="$(psql -h "$PGHOST" -U "$PGUSER" -d postgres -tA -c \
    "select coalesce(nullif(application_name, ''), '이름 없음') || ' ' || coalesce(host(client_addr), 'local')
       from pg_stat_activity where datname = '$PGDATABASE' and pid <> pg_backend_pid()")"
  if [ -n "$others" ]; then
    echo "❌ $PGDATABASE에 붙은 연결이 있습니다. 앱과 워커를 먼저 내립니다:"
    printf '%s\n' "$others" | sed 's/^/     /'
    echo "   infra/deploy/dc.sh stop web api worker worker-qc worker-gpu"
    exit 1
  fi
  echo "⚠ 운영 DB($PGDATABASE)를 덮어씁니다. 백업 시점 뒤의 변경은 사라집니다."
  printf '계속하려면 정확히 "덮어쓴다"를 입력: '
  read -r confirm
  [ "$confirm" = "덮어쓴다" ] || { echo "중단"; exit 1; }
  # pg_restore가 만든 SQL을 스키마 삭제와 함께 한 트랜잭션으로 붓는다. 첫 오류에서 멈추고 전부 되돌린다.
  # pg_restore --single-transaction만 쓰면 스키마 삭제가 따로 커밋되어, 붓다가 실패하면 빈 DB가 남는다.
  { printf 'DROP SCHEMA public CASCADE;\nCREATE SCHEMA public;\n'
    pg_restore --no-owner --exit-on-error -f - "$tmp/dump"
  } | psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" \
      -v ON_ERROR_STOP=1 --single-transaction -q -o /dev/null 2>"$tmp/restore.err" || {
    echo "❌ 복원 실패. 바뀐 것은 없습니다:"; grep -vE '^(NOTICE|DETAIL)|drop cascades' "$tmp/restore.err" | tail -5
    exit 1
  }
  echo "✅ 운영 DB 복원 완료"
  echo "   다음: infra/deploy/dc.sh --profile ops run --rm migrate 후 infra/deploy/dc.sh up -d"
  exit 0
fi

echo "▶ 검증 복원 → $CHECK_DB"
psql -h "$PGHOST" -U "$PGUSER" -d postgres -c "DROP DATABASE IF EXISTS $CHECK_DB;" >/dev/null
psql -h "$PGHOST" -U "$PGUSER" -d postgres -c "CREATE DATABASE $CHECK_DB;" >/dev/null
pg_restore -h "$PGHOST" -U "$PGUSER" -d "$CHECK_DB" --no-owner "$tmp/dump" >/dev/null

echo "▶ 행 수 비교 (운영 vs 복원본)"
q="select count(*) from videos"
for t in videos clips work_items media_stage_runs domain_events; do
  q="select count(*) from $t"
  a=$(psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -tAc "$q" 2>/dev/null || echo "-")
  b=$(psql -h "$PGHOST" -U "$PGUSER" -d "$CHECK_DB"   -tAc "$q" 2>/dev/null || echo "-")
  mark="✓"; [ "$a" = "$b" ] || mark="✗"
  printf '  %s %-18s 운영 %-8s 복원 %-8s\n' "$mark" "$t" "$a" "$b"
done

echo "검증 DB는 남겨 둡니다 — 확인 후 지우세요: DROP DATABASE $CHECK_DB;"
