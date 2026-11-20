# tests/auth

인증·계정 도메인(`services/auth`) 테스트입니다. 로그인·세션·JWT·관리자 계정 생성·계정 상태·개인정보 동의·최초 설정과 인증 경로 보호(rate limit·요청 크기·클라이언트 주소)를 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_admin_create_user.py` | 관리자 계정 생성(`POST /admin/users`)을 검사합니다. 만든 계정은 활성이고 재설정 링크로 비밀번호를 정하며 동의 전에는 업무 요청이 막힙니다. 이메일 중복(대소문자 무시)과 최고관리자 전용도 봅니다. |
| `test_admin_users.py` | 계정 상태가 `active`·`suspended` 두 값만 받는지 검사합니다. 상태 변경과 사용자 목록 `status` 필터의 422, 정지 계정 로그인의 403 `account_suspended`와 재활성화 뒤 로그인을 봅니다. |
| `test_auth_events.py` | 로그인·로그인 실패·로그아웃 이력을 검사합니다. auth.* domain_events와 본인 이력 조회(`/me/auth-events`)를 봅니다. |
| `test_auth_qa_guards.py` | 정지 계정 안내(맞는 비밀번호 403, 틀린 비밀번호 401)·계정 생성의 이름 규칙·토큰 갱신 한도·재설정 링크 상태(만료·사용·모름)와 동시 확정을 검사합니다. |
| `test_auth_security.py` | 인증 보안을 검사합니다. 로그인 잠금, 비밀번호 변경, 관리자 발급 재설정(정지 계정에는 409 `user_not_active`)을 봅니다. |
| `test_auth_rate_buckets.py` | 인증 경로의 IP별 실패 한도가 경로마다 따로 세어져 로그인 실패가 재설정 링크를 막지 않는지, Redis 카운터가 TTL 없이 남지 않고 실패 세기가 남은 초를 주는지 검사합니다. |
| `test_client_ip.py` | 클라이언트 주소는 한 가지 규칙으로 정합니다. 신뢰하는 프록시가 넘긴 값만 믿습니다. |
| `test_login_timing.py` | 없는 이메일의 로그인도 bcrypt 비교를 한 번 해 응답 시간으로 계정 존재 여부가 드러나지 않는지 검사합니다. |
| `test_login_throttle_ip_scope.py` | 로그인 잠금이 (이메일, IP) 쌍 5회와 이메일 단위 50회로 걸리는지, 재설정 확정의 전체 해제가 모든 IP를 풀고 glob 문자로 다른 이메일을 지우지 않는지 검사합니다. |
| `test_login_lock_event.py` | 이메일 단위 잠금이 걸린 실패에서 `auth.login_locked`가 한 줄 남는지, 잠금 중의 시도와 없는 이메일은 줄을 더하지 않는지(없는 이메일도 500 없이 401·429), 내용에 비밀번호·이메일 없이 IP·User-Agent·경로·실패 횟수·잠금 시간만 있는지, 비밀번호 변경 경로도 같은지, 본인 로그인 이력과 사용자 활동 이력에 보이는지, 두 백엔드가 잠금 시작에서만 참을 돌려주는지 검사합니다. |
| `test_login_throttle_email_case.py` | 로그인 실패 잠금이 이메일의 대소문자·공백 변형으로 풀리지 않는지 검사합니다. |
| `test_jwt_family.py` | JWT family 무효화와 Redis/LRU 폐기 백엔드를 검사합니다. |
| `test_logout_revokes_access.py` | 로그아웃이 Authorization 헤더의 access 토큰도 폐기하고, 서명이 맞지 않는 값은 무시하는지 검사합니다. |
| `test_logout_unknown_user.py` | 서명은 맞지만 이 DB에 없는 사용자의 refresh 토큰으로 로그아웃하면 204로 쿠키를 지우는지, 같은 토큰의 재사용 갱신이 500이 아니라 401인지, 두 경우 모두 감사 이벤트를 남기지 않는지 검사합니다. |
| `test_password_hash_off_loop.py` | 로그인·비밀번호 변경·재설정 확정의 bcrypt 계산이 이벤트 루프 스레드 밖에서 도는지 검사합니다. |
| `test_password_change_throttle.py` | 비밀번호 변경의 현재 비밀번호 실패가 로그인 잠금 카운터에 쌓이고 사용자별 실패 한도가 걸리는지 검사합니다. |
| `test_privacy_consent.py` | 개인정보 수집·이용 동의를 검증합니다. 미동의 계정의 업무 요청 차단과 동의 뒤 통과, 필수 동의 철회 거절, 선택 동의 없는 선택 항목 저장 거절, 프로필 개인정보 입력 규칙, 선택 동의 철회의 즉시 삭제, 감사 기록, 선택 동의 이행 마이그레이션의 왕복. |
| `test_rate_limit_body_size.py` | rate limiter와 요청 본문 크기 가드를 단위 테스트합니다. |
| `test_rate_limit_shared_office.py` | 한 공인 IP 뒤 50명 사무실 조건에서 성공한 로그인과 유효한 쿠키의 refresh가 429를 받지 않는지, 실패 로그인 연타 뒤에는 맞는 비밀번호도 429인지, 무효 쿠키는 IP 한도, 한 세션의 연타는 세션 한도, 비밀번호 변경은 사용자 한도에 걸리고 모든 429에 `Retry-After`가 있는지 검사합니다. |
| `test_refresh_cookie.py` | refresh 토큰 쿠키를 검사합니다. 로그인·refresh가 HttpOnly·SameSite=Strict·`Path=/api/auth` 쿠키를 주고 본문에 refresh 토큰을 싣지 않는지, 평문 http에는 `Secure`를 붙이지 않고 https 요청에만 붙이는지, refresh·logout이 다른 출처를 403으로 거절하는지, 로그아웃이 쿠키를 지우는지, 본문 토큰을 한 번 쿠키로 바꾸는지 봅니다. |
| `test_refresh_reuse.py` | refresh 재사용 탐지(유예 밖이면 family 폐기와 `auth.refresh_reused`, 유예 안이면 401만)와 동시 교체가 하나만 통과하는지, 두 폐기 백엔드의 처음 폐기 판정(Redis SET NX)을 검사합니다. |
| `test_session_hardening.py` | 로그아웃이 요청의 토큰과 같은 로그인(refresh family)의 교체된 토큰·access 토큰을 모두 끊고, 같은 계정의 다른 로그인은 두는지 검사합니다. |
| `test_session_max_age.py` | refresh 세션 절대 수명을 검사합니다. 교체가 로그인 시각(`auth_time`)을 옮기고 두 토큰의 만료가 세션 끝을 넘지 않는지, 절대 수명이 지나면 401인지, 클레임이 없는 기존 토큰은 발급 시각부터 세는지 봅니다. |
| `test_security.py` | 보안 회귀 케이스를 검증합니다. 72바이트를 넘는 한글 비밀번호, 검증되지 않은 토큰의 폐기 거부, JWT claim, 로그인 오류 문구, 운영 환경의 기본 secret 거부, CORS preflight, readyz 오류 문구, 중첩 헤더 토큰을 봅니다. |
| `test_setup.py` | 최초 설정 흐름을 검증합니다. |
| `test_stateless_backends.py` | 레플리카가 N개여도 보안 동작이 N=1일 때와 같은지 검증합니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
`test_auth_events.py`는 본인 이력 조회 라우터가 `workforce`에 있어도 테스트 대상이 인증 이력 기록이라 여기 둡니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
