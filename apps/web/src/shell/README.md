# shell

## 목적

로그인한 사용자의 화면을 감싸는 셸 부품을 둡니다. 역할에 맞춘 사이드바, 고정 상단바, 그 둘이 읽는 내비 표와 브레드크럼 규칙,
권한 가드와 안내 화면입니다. 이 부품들의 배치는 보호 셸(`app/_layout.tsx`)이 맡습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `AppSidebar.tsx` | 사이드바. 위에서부터 브랜드, 프로젝트 전환기, 개인 작업공간 링크 하나, 역할별 프로젝트 메뉴, 플랫폼 관리 링크(최고관리자, 플랫폼 화면 밖에서만), 하단 계정 메뉴(내 계정·다크 모드·플랫폼 관리·로그아웃)가 있습니다. 첫 설치에서 자격증명 연결과 사용자를 먼저 등록해야 하므로 플랫폼 관리를 계정 메뉴 밖에도 둡니다. 최고관리자가 플랫폼 화면에 있으면 전환기와 프로젝트 메뉴 대신 플랫폼 메뉴를 그립니다. 테마와 관계없이 흑연(`data-surface="graphite"`)으로 그립니다. |
| `TopBar.tsx` | 고정 상단바. 사이드바 접기와 브레드크럼만 둡니다. 세션 남은 시간과 전역 단축키 창은 없고, 단축키 창은 재생 키가 있는 작업 화면이 각자 엽니다. 페이지 이동 팔레트(⌘K)는 두지 않습니다. 화면 사이의 이동은 사이드바가 맡습니다. 탭 제목을 쓰는 곳은 이 부품 하나입니다(`useDocumentTitleWriter`) |
| `nav-config.ts` | 사이드바 내비 표. 프로젝트 메뉴 `SURFACES`, 개인 메뉴 `PERSONAL_PATHS`·`PERSONAL_LABEL`, 플랫폼 메뉴 `PLATFORM_ITEMS`, 단계 이름 `STAGE_LABEL`, 노출 판정 `surfaceVisible`·`surfaceLink`·`isPlatformPath`를 둡니다. 메뉴 항목마다 목적지 라우트 키(`route`)가 있고, 노출 여부는 `lib/roles`의 `ROUTE_ACCESS`로 정합니다 |
| `nav-config.test.ts` | 역할별 사이드바 항목의 노출·이름·활성 판정을 확인합니다 |
| `breadcrumbs.ts` | 경로와 맥락(주소의 프로젝트 이름·역할·화면이 등록한 제목)으로 브레드크럼을 만듭니다(`resolveBreadcrumbs`). 부모 마디는 그 역할이 열 수 있을 때만 링크로 두고, 모르는 경로는 `페이지를 찾을 수 없음`(`UNKNOWN_LABEL`)으로 씁니다 |
| `breadcrumbs.test.ts` | 라우터의 주소를 모두 풀어 보고 모르는 경로 이름이 나오지 않는지 확인합니다 |
| `ProjectSwitcher.tsx` | 사이드바 위쪽의 프로젝트 전환기. 고른 프로젝트를 `store/project`에 두고, 프로젝트를 바꾸면 보던 화면을 새 프로젝트로 엽니다(`switchTarget`). 고른 프로젝트가 없으면 `프로젝트 선택`이라고 쓰고, 참여 프로젝트가 하나뿐일 때만 그 프로젝트를 자동으로 고릅니다. 검색은 프로젝트가 6개를 넘을 때만 보여 줍니다 |
| `ProjectSwitcher.test.ts` | `switchTarget`을 확인합니다 |
| `PersonalHeader.tsx` | 개인 작업공간 머리글(`내 작업공간` h1, 프로젝트 칩, 액션, `PersonalTabs`) |
| `PersonalTabs.tsx` | 개인 메뉴 두 개(내 작업·내 활동)를 오가는 링크 줄 |
| `PersonalProjectChip.tsx` | 개인 작업공간이 따르는 프로젝트를 보여 주는 칩. 프로젝트를 따르는 화면(`PERSONAL_PROJECT_SCOPED`)에만 그리고, 고른 프로젝트가 없으면 `프로젝트 미선택`이라고 씁니다 |
| `ProjectStatusNotice.tsx` | 지금 보는 프로젝트가 활성이 아니면 본문 위에 상태와 막히는 작업(새 작업 배정)을 알리는 줄 |
| `RoleGuard.tsx` | 라우트 단위 권한 가드. 라우트 키(`route`)를 받아 `ROUTE_ACCESS`로 판정하고, 막히면 `AccessDenied`를 그립니다. 주소에 프로젝트가 없는 화면(`/review/:id`)은 그 역할로 참여한 프로젝트가 하나라도 있으면 들여보내고, 최종 판정은 서버에 맡깁니다 |
| `RoleLink.tsx` | 열 수 있는 사람에게만 링크를 그립니다. 같은 판정을 `useCanOpen(route)`로 내보내, 버튼을 숨기거나 403이 될 조회를 끌 때 씁니다 |
| `AccessDenied.tsx` | 막힌 화면의 안내. 막힌 이유(`reason`)에 따라 머리글과 문구를 정하고, 역할의 첫 화면(`lib/roles`의 `landingFor`)으로 가는 버튼을 붙입니다 |
| `RouteError.tsx` | 라우팅 실패 화면(`errorElement`·캐치올). 없는 주소와 그 밖의 실패를 구분하고, 돌아갈 곳으로 프로젝트 목록을 안내합니다 |

## 설계 이유

- 메뉴 항목(`nav-config`)과 경로 이름(`breadcrumbs`)을 데이터로 두고, 사이드바·상단바는 그리기만 합니다.
  화면마다 브레드크럼을 넘기게 하면 화면에 따라 브레드크럼이 있기도 하고 없기도 합니다. 이름을 두 곳에 적으면 한쪽만 낡습니다.
- 라우터 가드, 사이드바, 링크, 브레드크럼의 부모 마디가 모두 `ROUTE_ACCESS` 하나를 읽습니다. 표가 둘이면 링크를 눌렀을 때 접근 권한 없음 화면이 나옵니다.
  가드가 역할 배열을 직접 받지 않는 이유도 같습니다.
- 개인 작업공간(내 작업·내 활동)은 주소에 프로젝트가 없고 전환기 값을 따릅니다.
- 전환기는 사용자에게 알리지 않고 첫 프로젝트를 고르지 않습니다. 고르지 않은 것을 고른 것처럼 보여 주면 사용자는 엉뚱한 프로젝트의 수를 맞다고 믿습니다.
- 서버는 일시정지·종료된 프로젝트의 새 작업 배정을 409로 막습니다. 상태를 목록 칩에만 표시하면 다른 화면은 평소와 같아 보여서, 사용자는 다음 작업을 누른 뒤에야 막힌 것을 압니다.
  그래서 `ProjectStatusNotice`를 모든 화면 위에 둡니다.
- `AccessDenied`·`RouteError`는 역할 표와 스토어를 읽으므로 `shared/ui`가 아니라 이 폴더에 둡니다. 모양은 `shared/ui/StatePanel`이 정합니다.

## 의존성

- 부르는 것: `shared/ui/*`·`shared/hooks/*`·`shared/api/*`·`shared/lib/*`, `lib/roles`·`lib/role_label`, `hooks/useSlugProject`, `store/auth`·`store/project`·`store/theme`,
  `features/projects`(프로젝트 목록), `lucide-react`
- 부르는 쪽: `app/_layout.tsx`·`app/router.tsx`(가드·오류 화면), 기능 화면(`PersonalHeader`·`RoleGuard`·`RoleLink`·`AccessDenied`·`nav-config`의 이름표)

## 남은 일

- 없음.
