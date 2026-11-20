# hooks

## 목적

여러 기능이나 셸이 같이 쓰는 도메인 훅을 둡니다. 프로젝트·세션·재생 위치처럼 서버나 도메인 개념을 읽는 훅입니다.
도메인을 모르는 훅(`useOutsideClose`·`useDialogFocus`·`useDocumentTitle`·`useMediaQuery`·`useUnsavedGuard`)은 [`shared/hooks/`](../shared/hooks/README.md)에 있습니다.
한 기능만 쓰는 훅은 그 기능 폴더에 둡니다. 예를 들어 라벨링 워크벤치의 `useLabelWorkbench`는 `features/workbench/components/`에 있습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `useSlugProject.ts` | 주소의 `/p/:slug`에 맞춰 고른 프로젝트(`store/project`)를 바꿉니다. 맞춘 상태(`SlugState`: `none`·`syncing`·`ok`·`unknown`·`error`)를 돌려주고, 보호 셸이 본문을 그릴지 정하는 순수 판정 `layoutBody`·`nextShownSlug`를 둡니다. 주소에 프로젝트가 없는 개인 화면용 `useProjectParam`(`?project=`)과 `useSyncProjectContext`(연 작업의 프로젝트로 맞추기)도 여기 있습니다 |
| `useSlugProject.test.ts` | `layoutBody`와 주소 해석(`slugFromPath`·`projectParamFromUrl`)을 확인합니다 |
| `useAnalysisStack.ts` | 프로젝트의 분석 스택(`label`·구성·`usesVendorAnalyzer`)을 돌려줍니다. 벤더 분석기를 쓰는지는 프리셋 키가 아니라 서버가 주는 `adapter_key`로 판정합니다 |
| `useSessionKeepalive.ts` | 활동 중이면 access 토큰을 만료 2분 전에 백그라운드에서 갱신합니다. 30분 동안 활동이 없으면 갱신을 멈춰 세션을 만료시킵니다. 입력 이벤트(`keydown`·`pointerdown`·`scroll`·`wheel`·`touchstart`)를 활동으로 기록합니다 |

## 여기에 남은 이유

| 파일 | 쓰는 곳 | 남은 이유 |
|---|---|---|
| `useSlugProject.ts` | 보호 셸(`app/_layout`), 셸(`AppSidebar`·`TopBar`·`ProjectStatusNotice`), 작업 단계(검수 화면), 워크벤치 | 셸이 먼저 읽는 훅입니다. 프로젝트 목록(`features/projects`)을 읽지만, 주소와 고른 프로젝트를 맞추는 일은 셸이 맡습니다 |
| `useAnalysisStack.ts` | 프로젝트 설정, 통계, 등록, 영상 상세 | 네 기능이 쓰고, 이미 `features/projects`의 `index.ts`를 읽습니다 |
| `useSessionKeepalive.ts` | 보호 셸(`app/_layout`) | 세션 수명(인증)을 정하는 훅이라 도메인이 없는 `shared/hooks`에 두지 않습니다 |

## 설계 이유

- 주소의 slug가 고른 프로젝트보다 우선합니다. 그렇지 않으면 `/p/<slug>/...` 링크를 받은 사람이 마지막에 고른 다른 프로젝트를 보게 됩니다.
  목록에 없는 slug면 `unknown`을 돌려주고, 호출한 화면이 없는 프로젝트라고 표시합니다.
  맞추는 동안 본문을 미루지 않으면 화면들이 이전 프로젝트로 먼저 조회합니다. 반대로 이미 그린 본문을 잠깐 어긋났다고 내리면 저장하지 않은 편집이 사라집니다.
  그래서 `layoutBody`가 두 경우를 구분합니다.
- 재생 위치에는 함정이 세 가지 있어서 이 훅 한곳에서만 처리합니다.
  첫째, 클립 상대 시간은 클립 매핑이 안정되기 전에 잠깐 어긋나므로 raw `<video>`의 절대 시간을 읽습니다.
  둘째, 상대값에서 클립 시작을 한 번 더 빼면 재생헤드가 0에 멈춥니다.
  셋째, ref는 채워져도 알림이 오지 않으므로 플레이어가 붙을 때까지 다시 확인합니다.
  같은 코드를 화면마다 복사하면 두 번째 화면이 같은 함정에 다시 빠집니다. 시간 축 규칙은 `features/library`의 `VideoPlayer` 모듈 주석에 있습니다.
- 벤더 분석기가 없는 스택에 벤더 비용·처리량 카드를 띄우면 쓰지 않는 기능을 쓰는 것처럼 보입니다. 화면마다 판단하면 결과가 달라지므로 `usesVendorAnalyzer` 하나로 판정합니다.
- 세션 갱신의 활동에서 마우스 이동과 백그라운드 GET 폴링은 제외합니다. 이 둘을 활동으로 세면 아무도 보지 않는 탭이 세션을 계속 유지합니다.
  활동 정의와 토큰 보관 방식은 [`shared/api/README.md`](../shared/api/README.md)에 있습니다.

## 의존성

- 부르는 것: `react`, `react-router-dom`, `@tanstack/react-query`, `@vidstack/react`(플레이어 타입), `store/project`, `features/projects`(`useProjects`·`fetchAnalysisPresets`·`projectKeys`), `shared/api/auth`
- 부르는 쪽: 위 "여기에 남은 이유" 표

## 남은 일

- 없음.
