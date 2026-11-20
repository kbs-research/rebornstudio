# shared/hooks

## 목적

도메인을 모르는 React 훅을 둡니다. 이 훅들은 역할·프로젝트·작업 같은 서버 개념을 읽지 않고, 어느 화면에서 불러도 같은 일을 합니다.
`shared/ui`의 부품(`Modal`·`Drawer`·`DataTable`·`FilterMenu`·`PageHeader` 등)이 이 훅을 쓰므로 `shared/` 안에 둡니다.
도메인 개념을 읽는 훅(주소의 프로젝트, 재생 위치, 세션 갱신, 분석 스택)은 [`src/hooks/`](../../hooks/README.md)에 있습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `useOutsideClose.ts` | 바깥 클릭과 Escape로 팝오버·메뉴를 닫습니다. 포털 메뉴는 `extraRef`로 바깥 판정에서 빼고, `position: fixed` 메뉴는 `reposition`을 주면 스크롤·리사이즈 때 닫습니다. `returnFocus`를 주면 Escape로 닫을 때 포커스를 트리거로 돌려줍니다(판정 `focusWouldBeLost`) |
| `useOutsideClose.test.ts` | `focusWouldBeLost` 판정을 확인합니다 |
| `useDialogFocus.ts` | 열린 대화상자 안에 포커스를 가둡니다. 열리면 안쪽 첫 컨트롤로 포커스를 옮기고, `Tab`·`Shift+Tab`이 밖으로 나가지 않게 순환시키고, 닫히면 열기 전 위치로 포커스를 돌려놓습니다 |
| `useDocumentTitle.ts` | 브라우저 탭 제목(`{화면} · {프로젝트} · RebornStudio`). 화면은 이름을 등록만 하고(`useDocumentTitle`), 셸 상단바가 한곳에서 제목을 씁니다(`useDocumentTitleWriter`). 등록된 이름이 없으면 브레드크럼의 마지막 마디를 씁니다 |
| `useDocumentTitle.test.ts` | 탭 제목 형식과 이름 등록·해제를 확인합니다 |
| `useMediaQuery.ts` | CSS 미디어 쿼리 결과를 구독합니다. 폭에 따라 상태가 바뀌어야 할 때 씁니다(좁은 폭에서 사이드바 접기 버튼을 감추는 경우). 클래스만 바뀌면 되는 곳은 Tailwind 접두사를 씁니다 |
| `useUnsavedGuard.ts` | 저장하지 않은 편집이 있으면 탭 닫기·새로고침 때 브라우저 확인 창을 띄웁니다. 앱 안의 화면 이동은 다루지 않습니다 |

## 설계 이유

- 같은 효과를 파일마다 복사하면 한 곳을 고쳐도 나머지가 따라오지 않습니다. 바깥 클릭 닫기가 그런 경우였습니다.
  새 효과를 두 번째 파일에 붙여 넣게 되면 훅으로 분리합니다.
- 포털 메뉴를 바깥 판정에서 빼지 않으면 mousedown이 메뉴를 먼저 언마운트해서 항목 클릭이 전달되지 않습니다.
  Escape 뒤의 포커스 복귀를 별도 리스너에서 하면, 앞 리스너가 메뉴를 닫으면서 효과가 정리되어 복귀가 실행되지 않습니다. 그래서 한 훅 안에서 함께 처리합니다.
- `role="dialog" aria-modal="true"`는 나머지 페이지가 비활성이라는 뜻입니다. 포커스를 가두지 않으면 Tab으로 가림막 뒤의 요소에 닿고, 그 요소를 누를 수도 있습니다.
  배경에 `inert`를 걸지 않는 이유는 `Modal`·`Drawer`가 포털이 아니라 자기를 연 화면 안에 렌더되기 때문입니다. 배경에 `inert`를 걸면 대화상자도 함께 비활성이 됩니다.
  키 리스너는 캡처 단계에서 이벤트를 받으므로, 안쪽 부품이 `Tab`을 먼저 처리해도 포커스가 대화상자 안에 남습니다. `Escape`는 각 부품의 핸들러가 처리합니다.
- 탭 제목을 쓰는 곳이 둘이면 효과가 실행되는 순서에 따라 마지막 제목이 바뀝니다. 그래서 제목을 쓰는 곳을 상단바 하나로 둡니다.
  셸 밖 화면(로그인 등)에는 상단바가 없으므로, 등록된 이름을 이 훅이 바로 씁니다.
- 훅을 이 폴더에 둘지는 import로 정합니다. `shared/*`·`react`·`zustand` 밖을 읽으면 도메인 훅으로 보고 `src/hooks/`에 둡니다.
  `useSessionKeepalive`는 import는 가볍지만 세션 수명(인증)을 정하는 훅이라 `src/hooks/`에 둡니다.

## 의존성

- 부르는 것: `react`, `zustand`(등록된 탭 제목 저장소). `shared/` 밖은 가져오지 않습니다.
- 부르는 쪽: `shared/ui`의 대화상자·메뉴·표·머리글, 보호 셸(`app/_layout`), 셸(`TopBar`·`AppSidebar`·`ProjectSwitcher`), 대화상자를 직접 그리는 기능 부품(등록 시트, 워크벤치 확인창·문장 편집기), 편집 화면(검수·프로젝트 설정·라벨링 워크벤치)

## 남은 일

- 없음.
