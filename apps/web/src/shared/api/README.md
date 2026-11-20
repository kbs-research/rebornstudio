# shared/api

## 목적

서버를 부르는 입구 하나(`api()`)와, 그 입구가 쓰는 인증 토큰·세션 관리와 오류 문구 사전을 둡니다.
모든 화면이 같은 규칙으로 요청하고, 실패를 같은 문구로 보여 주게 하기 위한 폴더입니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `client.ts` | 타입이 붙은 `fetch` 래퍼 `api<T>()`와 파일 응답용 `apiFile()`. 둘은 같은 `send()`로 보냅니다. 본문이 `FormData`이면 JSON `Content-Type`을 붙이지 않습니다. Authorization 헤더를 붙이고, GET이 아닌 요청은 활동으로 기록합니다. 401을 받으면 유휴 상태가 아닐 때 refresh로 토큰을 다시 받아 한 번 재시도합니다. refresh가 401이면 세션을 비우고 `reborn:session-expired` 이벤트를 냅니다. refresh가 일시 오류(5xx·429·연결 실패)면 세션을 두고 원래 요청을 그 실패로 던집니다. 응답을 받지 못하면 `network_error`(상태 0)를 던집니다. 계약 타입 별칭(`UserDTO`·`ProjectDTO`…)도 여기서 내보냅니다 |
| `api_error.ts` | 실패 응답을 `ApiError`(`status`, 문자열 `detail`, 422의 필드별 사유 `fields`, `Retry-After` 값 `retryAfter`, 409 사용 중 거절의 막는 프로젝트 `projects`·`projectsMore`)로 바꾸는 `responseError`와 응답을 받지 못한 실패 `networkError`. `inUseProjects`는 실패 객체에서 막는 프로젝트를 꺼냅니다. `client.ts`의 요청과 `auth.ts`의 refresh가 같이 씁니다 |
| `api_error.test.ts` | 막는 프로젝트 이름과 나머지 수를 옮기고, 이름이 없는 실패와 JSON이 아닌 본문에는 프로젝트 칸을 두지 않는지 확인합니다 |
| `auth.ts` | 세션 토큰 관리. access 토큰은 메모리에만 두고, refresh 토큰은 서버가 HttpOnly 쿠키(`reborn_refresh`, `Path=/api/auth`)로 주고받아 화면이 읽지 않습니다. 앱 시작 때의 세션 복구(`restoreSession`. 일시 오류면 두 번 다시 시도하고 `restoreUnavailable`로, 429면 `restoreRetryAfter`로 대기 초를 알림), 동시에 들어온 refresh를 하나로 합치는 `refreshSession`(결과는 `ok`·`rejected`(401)·`unavailable`(일시 오류) 셋), 탭 사이 refresh 조율(Web Locks로 한 번에 하나, BroadcastChannel로 새 access 공유, 1초 안에 오지 않으면 직접 갱신), 활동 기록(`markActivity`·`isIdle`, 유휴 기준 `IDLE_LIMIT_MS` 30분), 만료 판정(`accessLapsed`), 서버 쪽 폐기(`revokeSession`), 다른 탭 로그아웃 알림(`announceSignOut`·`onSignedOutElsewhere`·`readSessionMessage`·`sessionEndedBySignOut`), 남은 시간 계산(`getSessionExpiryMs`)을 둡니다. 이전 버전 화면이 `localStorage`에 남긴 refresh 토큰은 한 번 본문으로 보내 쿠키로 바꾼 뒤 지웁니다 |
| `error_text.ts` | 서버 `detail` 코드를 화면 문구(굵은 결과 `message` + 작은 안내 `hint`)로 바꾸는 사전 `errorText`. `apiErrorText`는 실패 객체를 받아 422 필드 사유, 403 안내, 일시 오류 안내를 상태 코드에 따라 덧붙입니다. `isRetryable`은 다시 시도할 만한 실패(408·425·429·5xx·응답 없음)인지 판정합니다 |
| `error_text.test.ts` | 오류 문구 사전을 확인합니다 |
| `auth_refresh.test.ts` | 토큰이 브라우저 저장소에 남지 않는지, 새로고침 복구와 옛 저장소 토큰의 한 번 교환, 두 탭이 동시에 갱신해도 refresh가 한 번이고 로그아웃되지 않는지, 한 탭이 로그아웃하면 다른 탭이 토큰을 비우고 알림을 받으며 로그아웃 요청을 다시 보내지 않는지 확인합니다 |
| `refresh_failure.test.ts` | refresh가 401이면 세션을 지우고, 5xx·429·연결 실패면 세션을 두고 원래 요청을 그 실패로 돌리는지, `accessLapsed`가 일시 오류를 만료로 보지 않는지 확인합니다 |

## 설계 이유

- 화면은 `api()`를 직접 부르지 않고 `features/<도메인>/api.ts`의 함수를 거칩니다. `scripts/check-feature-boundaries.mjs --strict`가 규칙 1로 막습니다.
  경로 문자열이 화면마다 있으면 서버 경로가 바뀔 때 고칠 곳을 다 찾을 수 없습니다.
- `detail`은 항상 문자열로 둡니다. FastAPI 422의 `detail`은 배열로 옵니다.
  배열을 그대로 던지면 문구 사전이 `TypeError`를 내고, TanStack Query가 그 예외를 삼켜 저장 실패가 사용자에게 알려지지 않습니다.
  그래서 배열은 `detail = "validation_error"`와 `fields`로 나눕니다.
- 쿼리 자동 재시도와 화면의 다시 시도 안내가 같은 `isRetryable`을 씁니다. 403·404·409에 다시 시도를 안내하면 사용자가 눌러도 결과가 같습니다.
- 세션은 활동을 기준으로 갱신합니다. access 토큰은 수명을 짧게 두어 탈취됐을 때의 노출을 줄입니다. 활동 중이면 만료 전에 백그라운드에서 갱신해, 일하는 사람이 다시 로그인하지 않게 합니다.
  - 강한 활동 신호는 사용자가 시작한 변경 요청(GET이 아닌 요청)이고, `client.ts`가 기록합니다. 약한 신호는 입력 이벤트이고, `hooks/useSessionKeepalive.ts`가 기록합니다.
    마우스 이동과 백그라운드 GET 폴링은 활동으로 세지 않습니다. 이 둘을 세면 아무도 보지 않는 탭이 세션을 유지합니다.
  - 30분 동안 활동이 없으면 갱신을 멈추고, 세션은 그대로 만료됩니다.
  - 세션을 끝내는 것은 서버가 refresh를 401로 거절했을 때뿐입니다. 5xx·429·연결 실패에 로그아웃하면 서버가 잠깐 멈춘 것만으로 일하던 사람이 비밀번호를 다시 입력합니다.
  - 갱신 경로는 두 가지입니다. 만료 2분 전의 사전 갱신(`useSessionKeepalive`)과 401을 받은 뒤의 재시도(`client.ts`)입니다. 동시에 온 401은 `refreshSession`이 한 번의 refresh로 합칩니다.
- 로그아웃은 메모리만 비우지 않습니다. `revokeSession`으로 서버의 refresh 쿠키와 access 토큰(Authorization 헤더)을 폐기하고 쿠키를 지웁니다. 화면만 비우면 refresh 쿠키가 서버에서 계속 유효해 새로고침하면 세션이 돌아옵니다.
- 토큰을 브라우저 저장소에 두지 않습니다. 저장소에 두면 XSS가 생겼을 때 두 토큰이 바로 나갑니다. refresh 토큰은 HttpOnly 쿠키라 스크립트가 읽지 못하고, access 토큰은 메모리에만 있어 새로고침과 새 탭은 쿠키로 refresh를 한 번 부릅니다.
- 탭들은 refresh 쿠키 하나를 같이 쓰고 서버는 교체 때마다 쿠키를 바꿉니다. 두 탭이 같은 쿠키로 동시에 갱신하면 진 쪽이 401을 받으므로 Web Locks로 한 번에 하나만 보내고, 먼저 끝난 탭이 BroadcastChannel로 새 access 토큰을 나눕니다.
- 같은 채널로 로그아웃도 알립니다(`signed-out`). 알리지 않으면 다른 탭은 다음 요청의 401까지 로그아웃한 계정의 화면과 캐시를 보이고, 그 401을 세션 만료로 보고 비밀번호를 다시 받는 창을 띄웁니다. 받은 탭은 메모리의 토큰만 비우고 로그아웃 요청을 다시 보내지 않습니다. BroadcastChannel이 없는 브라우저에서는 다음 요청에서 만료 창이 뜹니다.

## 의존성

- 부르는 것: `@reborn/contracts`(요청·응답 타입). `shared/` 밖은 가져오지 않습니다.
- 부르는 쪽: 기능 폴더의 `api.ts`, 로그인 전 화면(`features/auth/pages/*`의 토큰 저장), `store/auth`, `hooks/useSessionKeepalive`, `app/_layout`·`app/queryClient`

## 남은 일

- 이전 버전의 저장소 토큰을 받는 호환 경로(본문 `refresh_token`)는 그 토큰의 절대 수명(14일)이 지나면 제거할 수 있습니다. 서버의 `auth.refreshed` 감사 기록에 `via=body`가 더 나오지 않는지 보고 정합니다.
