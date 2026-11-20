# shellcheck shell=sh
# `.env`가 켜는 compose 프로필을 계산한다. deploy.sh·dc.sh·Makefile이 source해서 쓴다.
#
# 켤 프로필은 사람이 기억하지 않고 `.env`가 정한다. 값을 채우면 다음 기동부터 그 서비스가 뜬다.
# 규칙이 두 곳에 있으면 한쪽만 고쳐진다. 배포는 pg-backup을 띄우는데 `dc.sh ps`에는
# pg-backup이 보이지 않는 식으로 어긋난다. 그래서 규칙 표는 이 파일에만 둔다.
#
# 주의: POSIX sh로 쓴다. Makefile의 recipe가 /bin/sh(dash일 수 있다)에서 source한다.
# `local`이 없으므로 함수마다 변수 이름을 따로 둔다.
# 호출하는 쪽이 `set -eu`여도 돌도록 조건은 `&&` 목록이 아니라 `if`로 쓴다.
# `&&` 목록은 마지막 항목만 errexit에서 면제되어, 첫 grep이 안 맞으면 함수가 거기서 끝난다.
#
# 쓰는 법:
#   . infra/deploy/profiles.sh
#   docker compose -f … $(compose_profiles .env) up -d
# 따옴표 없이 쓴다. 프로필 인자가 단어로 쪼개져야 한다. zsh는 `$(…)`를 쪼개지 않으므로
# sh나 bash에서 쓴다.

# 프로필과 그 프로필을 켜는 `.env` 키의 짝. 값이 비어 있지 않으면 켠다.
# ops(migrate)는 `run`으로만 부르므로 여기에 없다.
# `backup` 프로필은 개발 덮어쓰기(compose.dev.yaml)만 쓴다. 운영 pg-backup은 프로필 없이 늘 뜨므로
# 운영에서 이 규칙이 프로필을 켜도 달라지는 것이 없다.
# worker-gpu는 프로필 없이 늘 뜬다. 비식별화를 켠 프로젝트는 `.env`와 관계없이 이 워커가
# 있어야 처리된다.
COMPOSE_PROFILE_RULES="backup:BACKUP_BUCKET"

# `.env`가 그 프로필을 켜는지 판정한다.
#
# Args:
#   $1: env 파일 경로. 없으면 아무 프로필도 켜지 않는다.
#   $2: 프로필 이름.
# Returns:
#   켜면 0, 아니면 1.
profile_enabled() {
  for _pe_rule in $COMPOSE_PROFILE_RULES; do
    if [ "${_pe_rule%%:*}" = "$2" ]; then
      if [ -f "$1" ] && grep -qE "^${_pe_rule#*:}=.+" "$1"; then
        return 0
      fi
      return 1
    fi
  done
  return 1
}

# 켜진 프로필을 compose 인자(`--profile a --profile b`)로 출력한다. 없으면 빈 문자열이다.
compose_profiles() {
  _out=""
  for _cp_rule in $COMPOSE_PROFILE_RULES; do
    if profile_enabled "$1" "${_cp_rule%%:*}"; then
      _out="$_out --profile ${_cp_rule%%:*}"
    fi
  done
  printf '%s' "${_out# }"
}

# `.env`가 켜는 덮어쓰기 파일을 compose 인자(`-f …`)로 출력한다. 없으면 빈 문자열이다.
# 지금은 GPU 덮어쓰기 하나다. `GPU_ENABLED=true`이면 worker-gpu가 GPU 이미지와 GPU 예약을 쓴다.
# GPU가 없는 서버에서 이 파일을 얹으면 장치 예약 때문에 worker-gpu 컨테이너가 만들어지지 않으므로
# 자동 판별 대신 `.env` 값으로 켠다.
#
# Args:
#   $1: env 파일 경로.
#   $2: 저장소 루트(compose 파일이 있는 곳).
compose_files() {
  if [ -f "$1" ] && grep -qE "^GPU_ENABLED=true$" "$1"; then
    printf '%s' "-f $2/infra/compose/compose.gpu.yaml"
  fi
}
