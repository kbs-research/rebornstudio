# app

## 목적

앱을 조립하는 폴더입니다. 라우트 트리, 로그인한 사용자의 화면을 감싸는 보호 셸, 전역 주소 리다이렉트, 전역 공급자, 쿼리 클라이언트를 둡니다.
화면과 부품은 두지 않습니다. 화면은 `features/<도메인>/pages/`에, 셸 부품은 `shell/`에 있습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `router.tsx` | `createBrowserRouter`로 전체 라우트 트리를 만듭니다. 인증 전 화면(`features/auth`의 `authRoutes`)을 먼저 두고, 나머지는 `ProtectedLayout` 아래에서 기능 폴더의 라우트 배열을 펼칩니다. 디자인 시스템 견본(`/design-system`)은 `import.meta.env.DEV`일 때만 등록합니다 |
| `_layout.tsx` | 보호 셸 `ProtectedLayout`. 로그인하지 않은 사용자는 `/login`으로 보내고, 개인정보 동의 기록이 없으면 셸 대신 동의 화면(`PrivacyConsentPage`)을 그립니다. 사이드바·상단바·본문을 배치합니다. 앱 시작 때 세션 복구가 서버 일시 오류로 끝났으면 로그인 화면 대신 서버 연결 실패 패널을, 요청 한도(429)로 끝났으면 서버가 준 대기 시간을 센 뒤 다시 여는 요청 한도 초과 패널을 그립니다(`RestoreFailedPanel`). 세션이 만료되면 다시 로그인 창을 띄우고 비밀번호를 다시 받아 같은 화면을 이어 갑니다. 이 탭이나 다른 탭에서 로그아웃해 끝난 세션은 그 창 없이 돌아올 주소 없는 `/login`으로 보내고(판정은 `auth_gate.ts`), 정지·거절된 계정에는 사용할 수 없는 계정 패널을 띄웁니다. 주소의 프로젝트와 고른 프로젝트를 맞추는 동안에는 본문 대신 로딩 골격을 그립니다(`layoutBody`) |
| `auth_gate.ts` · `auth_gate.test.ts` | 보호 셸의 인증 가드 판정 `authGate`. 셸·연결 실패 패널·로그인 주소 중 하나를 고르고, 로그아웃으로 끝난 세션은 `/login`으로, 그 밖에 토큰이 없으면 돌아올 주소를 붙인 `loginPath`로 보냅니다. 이 탭의 로그아웃과 다른 탭의 신호가 다시 그려지는 순서와 관계없이 같은 주소로 끝나도록 세션 상태로 정합니다 |
| `redirects.tsx` | slug 없는 전역 `/review`를 선택한 프로젝트의 작업 단계로 보내는 `ToPipeline`. `features/pipeline/routes.tsx`가 씁니다 |
| `providers.tsx` | `AppProviders`. 쿼리 클라이언트와 토스트를 공급합니다. 쿼리의 전역 실패 알림이 토스트를 쓰므로 토스트 공급자를 안쪽에 둡니다 |
| `queryClient.ts` | TanStack Query 클라이언트. 기본 `staleTime`은 30초이고, 재시도 여부는 `shared/api/error_text`의 `isRetryable`로 정합니다. 뮤테이션 실패와 첫 조회 실패는 전역 토스트로 알립니다. 셸이 따로 알리는 실패(401 세션 만료, 403 `account_not_active`, 403 `privacy_consent_required`)는 `shellHandles`가 걸러 냅니다 |
| `queryClient.test.ts` | `shellHandles`가 셸이 맡는 실패만 걸러 내는지 확인합니다 |

## 설계 이유

- 라우트 트리는 한 파일에서 조립하고, 경로 묶음은 각 기능의 `routes.tsx`에 둡니다. 트리의 순서와 가드 묶음은 한곳에서 보여야 합니다.
  화면을 고치는 사람은 자기 기능의 배열만 열면 됩니다. 어느 경로가 어느 배열에 있는지는 [`routes/README.md`](../routes/README.md)에 있습니다.
- 권한 가드(`shell/RoleGuard`)는 라우트 키만 받고, 역할 목록은 `lib/roles.ts`의 `ROUTE_ACCESS`에 둡니다.
  가드·링크·사이드바가 같은 표를 읽어야 열 수 없는 화면으로 가는 링크가 생기지 않습니다.
- 전역 실패 알림이 없으면 제출이나 초안 저장이 429·5xx로 실패해도 화면에 아무것도 나타나지 않습니다. 그러면 사용자는 저장됐다고 생각합니다.
  반대로 백그라운드 재조회 실패까지 알리면 토스트가 30초마다 쌓입니다.
  그래서 첫 조회 실패만 알리고, 화면이 직접 그리는 실패는 `meta.handled`로 제외합니다.
- 오류 화면(`RouteError`)은 보호 셸의 `errorElement`로 둡니다. 셸 밖에 두면 사이드바까지 사라져, 잘못 들어온 사람이 다른 화면으로 나갈 방법이 없습니다.

## 의존성

- 부르는 것: `react-router-dom`, `@tanstack/react-query`, `shell/*`, `shared/*`, `hooks/useSlugProject`·`hooks/useSessionKeepalive`, `store/auth`·`store/project`,
  `features/*/routes`(라우트 배열), `features/auth`(`login`·`PrivacyConsentPage`), `routes/design_system`
- 부르는 쪽: `main.tsx`(`router`·`AppProviders`), `store/auth`·`features/auth/pages/login.tsx`(`queryClient`로 캐시 비우기), `features/pipeline/routes.tsx`(`redirects`)

## 남은 일

- 없음.
