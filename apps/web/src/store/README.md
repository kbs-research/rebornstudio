# store

## 목적

앱 전체가 읽는 클라이언트 상태를 Zustand 스토어로 둡니다. 로그인 사용자, 고른 프로젝트, 테마 세 가지입니다.
서버 상태(목록·상세·통계)는 TanStack Query가 맡으므로 이 폴더에 두지 않습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `auth.ts` | `useAuth`. 로그인 사용자(`user`)와 조회 실패(`error`)를 둡니다. `loadMe()`로 불러오고(`features/auth`의 `fetchMe`) `signOut()`으로 비웁니다. 로그아웃하면 서버의 refresh 토큰을 폐기하고(`revokeSession`) 다른 탭에 알린 뒤(`announceSignOut`) 토큰과 쿼리 캐시를 지웁니다. 다른 탭에서 로그아웃했다는 신호(`onSignedOutElsewhere`)를 받아도 사용자 상태와 쿼리 캐시를 지웁니다 |
| `project.ts` | `useSelectedProject`. 고른 프로젝트 id를 `localStorage`(`reborn.project`)에 저장합니다. `requestProjectSwitcher`는 프로젝트 선택이 필요한 빈 상태가 사이드바의 전환기를 열 때 보내는 신호이고, 저장하지 않습니다 |
| `project.test.ts` | `requestProjectSwitcher`를 부를 때마다 신호 값이 바뀌는지 확인합니다 |
| `theme.ts` | `useTheme`. 다크·라이트 테마를 `localStorage`(`reborn.theme`)에 저장하고 문서 루트의 `data-theme` 속성과 맞춥니다. 저장한 값이 없으면 운영체제 설정(`prefers-color-scheme`)을 따릅니다. 계정 메뉴의 다크 모드 스위치가 씁니다 |

## 여기에 남은 이유

세 스토어 모두 앱 전체가 읽습니다. 로그인 사용자는 셸과 대부분의 기능이, 고른 프로젝트는 셸과 여러 기능이 읽습니다.
테마는 진입점(`main.tsx`)이 import하는 즉시 적용됩니다. 한 기능에 넣으면 나머지 기능이 모두 그 기능의 `index.ts`를 거쳐 읽어야 합니다.
워크벤치에서만 켜고 끄는 면 범위(`useWorkbenchSurface`)는 `features/workbench/store.ts`에 있습니다.

## 설계 이유

- 스토어를 하나로 합치지 않고 상태마다 나눕니다. 화면은 필요한 스토어만 구독하고, 저장 여부(`persist`)도 스토어마다 따로 정합니다.
- `loadMe()`의 실패는 `error`로 남깁니다. 실패를 삼키면 셸은 `user`가 `null`인 것만 보고 계속 불러오는 중이라고 판단합니다.
- 전환기 열기 신호는 참·거짓 값이 아니라 증가하는 `nonce`입니다. 참·거짓 값이면 이미 참일 때 두 번째 요청이 값을 바꾸지 못해, 닫힌 전환기가 다시 열리지 않습니다.
- 테마는 스토어를 import하는 시점에 문서 루트에 바로 적용합니다. 렌더 뒤에 적용하면 잘못된 테마가 잠깐 보입니다.
  워크벤치는 이 값과 관계없이 흑연 테마입니다([`styles/README.md`](../styles/README.md)).

## 의존성

- 부르는 것: `zustand`, `zustand/middleware`(`persist`: `project.ts`·`theme.ts`), `features/auth`(`fetchMe`), `shared/api/auth`, `app/queryClient`
- 부르는 쪽: 셸, 보호 셸, 기능 화면 전반, `lib/anon.ts`, `routes/design_system.tsx`(테마)

## 남은 일

- 없음.
