# services/auth

인증과 계정 도메인입니다. 계정 생성, 로그인, 토큰 갱신, 로그아웃, 세션 무효화, 인증 경로의 남용 제한, 프로필과 개인정보 동의, 최초 설정을 맡습니다. 공개 가입은 없고 계정은 super_admin이 만듭니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `service.py` | 로그인·토큰 갱신·로그아웃·비밀번호 변경, 재설정 링크 상태 확인(`check_password_reset`)과 확정. 링크 유효 시간 `RESET_LINK_VALID_MINUTES`. 이메일 단위 잠금이 걸린 실패에서 `auth.login_locked` 감사 기록을 남깁니다 |
| `admin.py` | super_admin 작업. 계정 생성(`create_user`, 응답에 비밀번호 설정 링크), 역할·상태 변경, 비밀번호 재설정 링크 발급(`issue_password_reset`, 서버 명령과 같이 쓰는 `issue_reset_token`) |
| `setup.py` | 최초 설정. 활성 super_admin이 없으면 미초기화로 판정하고, 셋업 토큰을 발급·검증해 첫 super_admin을 만듭니다 |
| `consent.py` | 개인정보 필수 동의의 판정과 기록. 업무 API 가드(`require_present`), 동의 요청의 필수 동의 확인, 이전 선택 동의 철회 시 선택 항목 삭제 |
| `user.py` | 내 프로필 수정, 필수 동의 기록(`consent_me`), 선택 동의 철회(`withdraw_optional_me`), super_admin용 사용자 목록 |
| `session_cookie.py` | refresh 토큰 쿠키(`reborn_refresh`, HttpOnly·SameSite=Strict·`Path=/api/auth`, 요청이 https일 때만 `Secure`)를 싣고 지우고 읽습니다. refresh·logout의 출처 확인(`require_same_origin`, 허용 출처는 `CORS_ORIGINS`와 서버 자신의 출처)도 둡니다 |
| `jwt_revocation.py` | 토큰 폐기 백엔드. `jti` 단위와 refresh family 단위로 폐기합니다. `revoke_jti`는 처음 폐기했는지를 돌려주고 폐기 시각을 남깁니다. Redis가 있으면 Redis를 쓰고, local·test는 인메모리 LRU를 씁니다 |
| `login_throttle.py` | 로그인 실패 제한. (이메일, 클라이언트 IP) 쌍은 연속 5회, 이메일 하나는 IP와 관계없이 50회 실패하면 15분 동안 잠급니다. 이메일은 공백을 떼고 소문자로 맞춥니다. `record_failure`는 이메일 단위 잠금이 그 호출로 시작됐는지 돌려줍니다 |
| `rate_limit.py` | 인증 경로의 요청 한도. `auth_failures`(로그인·재설정 확인·확정의 실패를 IP로, 비밀번호 변경의 실패를 사용자로 세고 경로마다 버킷이 따로), `refresh_failures`(쿠키가 없거나 무효인 refresh를 IP로), `refresh_session_hit`(서명이 확인된 refresh를 로그인 세션으로), `invite_limit`(초대를 사람으로). 모든 429에 `Retry-After`를 싣습니다. Redis 카운터는 INCR와 `EXPIRE NX`를 한 트랜잭션으로 보냅니다 |

## 설계 이유

- 비밀번호는 SHA-256으로 사전 해시한 뒤 bcrypt로 해시합니다. bcrypt는 72바이트 뒤를 버립니다. 사전 해시가 없으면 긴 한글 비밀번호의 뒷부분이 검사되지 않습니다(`api_gateway/security.py`).
- bcrypt 해시 계산과 비교(rounds 12, 한 번에 약 0.25초)는 스레드에서 돕니다(`service._off_loop`). API는 uvicorn 프로세스 하나라서 이벤트 루프에서 돌리면 출근 시각에 로그인이 몰릴 때 다른 요청이 모두 그 시간만큼 멈춥니다. 로그인·비밀번호 변경·재설정 확정·최초 설정이 모두 같은 방식입니다.
- 토큰 폐기에는 두 단위가 있습니다. `jti`는 토큰 하나를 무효로 하고, refresh family는 같은 로그인에서 이어진 토큰 전부를 무효로 합니다. refresh 교체는 이전 jti를 처음 폐기한 요청(Redis SET NX)만 통과합니다. 폐기된 refresh 토큰이 유예 시간(`REFRESH_REUSE_GRACE_SECONDS`, 30초)을 넘겨 다시 오면 탈취로 보고 family 전체를 끊습니다(`auth.refresh_reused`). 유예 시간은 여러 탭이 같은 토큰으로 거의 동시에 갱신하는 경우를 위해 둡니다.
- refresh 토큰은 본문에 싣지 않고 HttpOnly 쿠키로만 줍니다. 화면은 access 토큰을 메모리에만 두므로 XSS가 생겨도 refresh 토큰은 나가지 않습니다. 쿠키가 자동으로 붙는 refresh·logout은 `Origin`(없으면 `Referer`)이 허용 출처가 아니면 403 `origin_not_allowed`입니다. 허용 출처는 `CORS_ORIGINS`와 서버 자신의 출처(요청 scheme과 `Host`)입니다. 쿠키가 없으면 본문 `refresh_token`을 한 번 받아 쿠키로 바꿉니다. 브라우저 저장소에 refresh 토큰이 남아 있는 사용자를 위한 호환 경로이고 `auth.refreshed`의 `via`로 셉니다.
- 쿠키의 `Secure`는 환경 이름이 아니라 요청 scheme으로 정합니다. 기본 배포가 TLS 없이 80을 열어서, 환경 이름으로 붙이면 평문 http에서 브라우저가 쿠키를 저장하지 않아 새로고침마다 로그인이 풀립니다. scheme은 uvicorn `--proxy-headers`가 신뢰 프록시(`FORWARDED_ALLOW_IPS`, 운영은 web과 api만 붙는 edge 서브넷)의 `X-Forwarded-Proto`만 반영한 값입니다. web nginx는 믿는 앞단(`WEB_TRUSTED_PROXIES`)이 보낸 `X-Forwarded-Proto`만 넘기므로, 사용자가 직접 보낸 `https`는 쿠키 판정에 닿지 않습니다.
- 로그인 세션에는 절대 수명(`JWT_SESSION_MAX_DAYS`, 기본 14일)이 있습니다. refresh 토큰은 로그인 시각(`auth_time` 클레임)을 교체 때 그대로 옮기고, 그 시각에서 절대 수명이 지나면 refresh가 401입니다. 교체가 refresh 토큰의 만료를 계속 미루므로 이 상한이 없으면 탈취한 토큰으로 세션을 무기한 잇습니다. 클레임이 없는 기존 토큰은 발급 시각(`iat`)부터 셉니다. 로그아웃은 요청의 토큰과 그 family를 함께 폐기합니다.
- `login_throttle`은 계정 단위 실패 누적을 세고, `rate_limit`은 IP 단위 요청량을 셉니다. 막는 공격이 다르므로 두 모듈로 둡니다.
  - 실패 잠금의 5회 임계를 이메일 하나에 걸면 남의 이메일로 5번 틀리는 것만으로 그 사람의 로그인을 막을 수 있습니다. 그래서 5회 임계는 (이메일, IP) 쌍에 겁니다. IP를 바꿔 가며 시도하는 대입 공격은 이메일 단위 50회 임계로 막습니다.
  - 한 사무실이 NAT 뒤에서 IP 하나를 같이 쓰는 것을 기본 조건으로 둡니다. 그래서 IP로는 실패만 세고(기본 분당 10, 경로마다 따로), 인증이 확인된 요청은 사용자나 로그인 세션으로 셉니다. 성공한 로그인과 유효한 쿠키의 refresh는 IP 한도를 쓰지 않습니다.
  - 실패가 한도에 닿으면 창이 끝날 때까지 그 IP의 맞는 비밀번호도 429입니다. 성공을 통과시키면 대입 공격이 한도를 넘은 뒤에도 성공 응답으로 비밀번호를 확인할 수 있기 때문입니다.
  - refresh는 서명된 토큰이라 대입으로 맞힐 수 없습니다. 그래서 무효 토큰의 실패(기본 분당 300)만 IP로 세고 한도를 넘은 실패에만 429를 돌려줍니다. 서명이 확인된 refresh는 로그인 세션마다 분당 60으로 셉니다.
- Redis 카운터의 INCR와 EXPIRE는 한 트랜잭션으로 보냅니다. 따로 보내면 사이에 연결이 끊긴 키가 TTL 없이 남아 그 IP나 이메일을 영구히 막습니다. `EXPIRE NX`라서 이미 TTL 없이 남은 키도 다음 요청에서 TTL을 받습니다.
- 이메일 단위 잠금은 맞는 비밀번호도 막고, 남의 이메일로 여러 IP에서 틀리기만 해도 걸립니다. 본인이 이유를 모른 채 15분 동안 로그인하지 못하므로 잠금이 시작된 실패에서 `auth.login_locked`를 한 줄 남깁니다(대상과 행위자는 그 계정, 내용은 IP·User-Agent·경로 `login`/`password_change`·실패 횟수·잠금 시간). 본인 로그인 이력과 관리자의 사용자 활동 이력이 이 줄을 보여 줍니다. Redis 백엔드는 잠금 키를 `SET NX`로 만들어 레플리카가 여럿이어도 잠금 하나에 한 줄만 남습니다. 잠금 중의 시도는 429로 끝나 실패를 세지 않으므로 줄이 늘지 않습니다. 쌍 단위 잠금은 그 IP만 막으므로 기록하지 않고, 없는 이메일은 막히는 계정이 없어 기록하지 않습니다.
- 세 모듈의 카운터와 폐기 목록은 Redis에 둡니다. 프로세스 메모리에 두면 레플리카가 N개일 때 잠금 임계가 N배로 느슨해지고, 로그아웃한 토큰이 다른 레플리카에서 통과합니다. local·test가 아닌 환경에서 Redis가 없으면 `redis_infra`가 기동을 실패시킵니다.
- 로그인은 없는 이메일과 틀린 비밀번호에 같은 401 `invalid_credentials`를 돌려줍니다. 비밀번호가 맞으면 계정 상태를 알립니다. 정지 계정은 403 `account_suspended`입니다.
- 공개 가입을 두지 않습니다. 고용된 라벨러를 운영하는 도구라 계정을 만들 사람은 관리자이고, 가입 신청과 승인 단계는 관리자가 계정을 만드는 것보다 한 단계가 많습니다. 만든 계정은 비밀번호가 없고, 응답의 1회용 링크로 본인이 비밀번호를 정합니다.
- 초기화 여부는 플래그 대신 활성 super_admin 수로 판정합니다. 플래그는 실제 상태와 어긋날 수 있기 때문입니다. 셋업 토큰은 서버 로그에만 남깁니다. 그래서 서버에 접근할 수 있는 사람만 첫 관리자를 만들 수 있습니다.
- 필수 동의는 계정 사용의 조건이므로 철회를 받지 않습니다. 철회를 원하면 계정 삭제 요청으로 처리합니다. 선택 항목(전화번호·생년월일·주소)과 새 선택 동의는 받지 않습니다. 이전에 선택 동의한 계정은 언제든 철회할 수 있고, 철회하면 선택 항목을 바로 지웁니다.

## 의존성

- 부르는 것: `models.User`·`PasswordResetToken`, `api_gateway.security`(해시·토큰), `api_gateway.redis_infra`, `services.audit`, 허용 목록의 `work.service`(계정 정지 때 보유 풀기, `release_holds`)
- 부르는 쪽: `api/v1/auth.py`·`users.py`·`admin.py`·`setup.py`, `api_gateway/dependencies.py`(`consent`), `api_gateway/security.py`(`jwt_revocation`), `api_gateway/main.py`(셋업 토큰 발급), `api_gateway/cli/recover_admin.py`(`issue_reset_token`)
- 설정: `JWT_SECRET`·`JWT_REFRESH_SECRET`·`JWT_SESSION_MAX_DAYS`, `RATE_LIMIT_AUTH_PER_MIN`·`RATE_LIMIT_REFRESH_PER_MIN`·`RATE_LIMIT_REFRESH_SESSION_PER_MIN`, `CORS_ORIGINS`, `REDIS_URL`. 키의 뜻은 `docs/env-catalog.md`에 있습니다.

## 남은 일

- 없음.
