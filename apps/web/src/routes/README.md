# routes

## 목적

라우터에 등록된 화면 가운데 도메인이 없는 화면을 둡니다. 지금은 디자인 시스템 견본 화면 하나입니다.
도메인 화면은 `features/<도메인>/pages/`에, 그 라우트 배열은 `features/<도메인>/routes.tsx`에, 트리 조립은 `app/router.tsx`에 있습니다.
이 README에는 경로 묶음이 어느 파일에 있는지 찾는 표와 새 화면을 만들 때 확인할 항목도 둡니다.

## 주요 파일

| 파일 | 경로 | 역할 |
|---|---|---|
| `design_system.tsx` | `/design-system` | 토큰(면·글자·액센트·상태·글자 단계·모서리)과 관례를 실제 부품으로 보여 주는 견본 화면입니다. 라이트·다크 전환으로 두 테마를 확인합니다. 로그인 없이 열리고, 개발 빌드에서만 등록됩니다(`app/router.tsx`의 `import.meta.env.DEV` 분기) |

## 여기에 남은 이유

- 도메인이 없어서 `features/<도메인>/` 아래에 둘 곳이 없습니다.
- `shared/`는 `shared/` 밖을 읽지 않는데, 이 화면은 테마 스토어(`store/theme`)와 상태 칩(`components/StatusBadge`)을 읽습니다.
- `app/`은 앱을 조립하는 폴더라서 화면을 두지 않습니다.

## 설계 이유

- 견본에는 흉내 낸 샘플이 아니라 실제 부품을 둡니다. 사람들은 견본에서 모양을 복사해 가므로, 흉내 낸 샘플이 틀리면 틀린 모양이 퍼집니다.
- 운영 빌드에서는 라우트와 화면 모듈이 번들에서 빠집니다. 가드로 막으면 코드가 번들에 남고, 공개 주소로 두면 부품 목록과 토큰 이름이 외부에 드러납니다.
- 견본의 카드는 평평하게 그립니다. `scripts/check-surface.mjs`의 `FLAT_OK`가 사유와 함께 이 파일을 허용합니다.

## 라우트는 어디에 있나

| 경로 묶음 | 라우트 배열 |
|---|---|
| `/login`·`/password-reset`·`/setup`·`/privacy` | `features/auth/routes.tsx` |
| `/`(홈)·`/my/work`·`/my/activity`·`/account` | `features/me/routes.tsx` |
| `/projects`·`/projects/new`·`/p/:slug/settings` | `features/projects/routes.tsx` |
| `/p/:slug/overview`·`/p/:slug/insights` | `features/insights/routes.tsx` |
| `/p/:slug/content`·`/p/:slug/videos/:videoId`·`/p/:slug/videos/:videoId/clips` | `features/library/routes.tsx` |
| `/p/:slug/pipeline`·`/review/:id`·`/p/:slug/qc`·`/p/:slug/completed/:workItemId`, 전역 `/review` | `features/pipeline/routes.tsx` |
| `/work/:id/label`·`/p/:slug/completed` | `features/workbench/routes.tsx` |
| `/p/:slug/people` | `features/people/routes.tsx` |
| `/admin`·`/admin/users`·`/admin/connections` | `features/platform/routes.tsx` |
| `/design-system`, `/p/:slug`(개요로 보냄), 캐치올 `*` | `app/router.tsx` |

정확한 경로·가드·순서는 `app/router.tsx`와 각 `routes.tsx`의 코드를 기준으로 합니다. 이 표는 어느 파일을 열지 찾을 때 씁니다.

## 새 화면을 만들 때

새 화면은 `features/<도메인>/pages/`에 만듭니다.

| 항목 | 지킬 것 | 확인 |
|---|---|---|
| 제목 구조 | 절 제목은 heading 요소로 씁니다. `SectionHeader`·`TableCard`는 기본으로 `h2`를 냅니다. 대화상자는 `Modal`·`Drawer`가 제목을 접근 가능한 이름으로 씁니다 | `check-headings --strict` |
| 실패와 빈 상태 | 조회 실패와 빈 결과를 구분합니다. `DataTable`·`TableCard`로 그리는 표는 `error={q.isError}`를 넘기고, 그 밖에는 `q.isError ? <ErrorState onRetry=… /> : <EmptyState … />`로 씁니다. 빈 상태가 조회 결과가 아닌 경우(권한·역할)는 `EXPECTED`에 사유와 함께 적습니다 | `check-query-states --strict` |
| 지원 폭 | 가로 1280px에서 한 번 열어 넘치거나 잘리는 곳이 없는지 봅니다. 잘린 곳을 찾으면 넓은 폭에서도 다시 잽니다. 값이 같으면 폭 문제가 아니라 고정폭 문제입니다 | 사람 |
| 키보드 경로 | 반복 작업에 키보드 경로가 있는지 봅니다. 빠지기 쉬운 곳은 편집기를 열고 닫는 것처럼 모드에 들어갔다 나오는 동작입니다. 워크벤치는 `WorkbenchHeader`를 써서 셸을 `inert`로 만듭니다 | 사람 |
| 목록 골격 | 위에서부터 `PageHeader`(또는 `PersonalHeader`), 툴바(수·검색·필터), 본문(`TableCard`나 카드 목록), 페이지 컨트롤 순서로 둡니다. 툴바는 `<SectionHeader bare count=… action=… />`로 만듭니다. 수는 분모를 밝힙니다. 조회 상한에 닿으면 `최신 N건 · 더 있을 수 있음`, 커서 페이지면 `앞 N건 · 더 있음`으로 씁니다 | 사람 |
| 역할별 확인 | 그 화면을 쓰는 역할의 계정으로 엽니다(`make dev-token EMAIL=…`). 관리자 계정으로는 역할 분기가 보이지 않습니다 | 사람 |
| 문구 | 1인칭·`벤더`·마크다운·반말 종결을 쓰지 않습니다. 비가역 액션의 확인창에는 무엇이 사라지고 무엇이 남는지, 되돌릴 수 있는지를 같은 화면에 씁니다. 검사는 문장이 있는지만 보므로, 쓰기 전에 서버가 실제로 하는 일을 코드에서 확인합니다 | `check-copy`·`check-danger-copy --strict` |

## 의존성

- `design_system.tsx`: `shared/ui/*`, `shared/hooks/useDocumentTitle`, `shared/lib/cn`, `components/StatusBadge`, `store/theme`
- 부르는 쪽: `app/router.tsx`(개발 빌드)

## 남은 일

- 없음.
