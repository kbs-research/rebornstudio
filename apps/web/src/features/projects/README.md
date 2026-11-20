# features/projects

## 목적

프로젝트 기능을 담당합니다. 프로젝트 목록(`/projects`)·생성(`/projects/new`)·설정(`/p/:slug/settings`) 화면이 여기 있습니다.
프로젝트 하나의 성격을 보여 주는 부품(분석 스택·진척률·연결 안내·주소의 프로젝트를 찾지 못했을 때의 상태), 설정 폼의 규칙, 앱 전체가 읽는 프로젝트 목록·단건 조회도 이 폴더에 둡니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `api.ts` | 서버 호출(목록·단건·생성·저장·삭제·통계, 분석 스택 카탈로그, 분석 연동 진단(읽기 전용), 연결 선택지 `fetchConnectionChoices`, 저장소·분석 대상의 선택지·저장·점검)과 키 팩토리 `projectKeys`, 조회 훅 `useProjects`(목록)·`useProject`(단건) |
| `routes.tsx` | 라우트 세 묶음. `app/router.tsx`가 목록은 일반 멤버 라우트에, 생성은 생성 가드(`projectNew`)에, 설정은 설정 가드(`settings`) 안에 펼칩니다 |
| `index.ts` | 밖으로 내보내는 것: 조회 훅·`projectKeys`·`fetchProject`·`fetchAnalysisPresets`, 부품(`ConnectionNotice`·`ProjectShellState`), 목록 화면(`ProjectsPage`, 홈이 그림), 설정 필드 이름(`FIELD_META`, 감사 이벤트 표), 상태 이름(`PROJECT_STATUS_LABEL`). 라우트는 내보내지 않습니다 |
| [`pages/`](./pages/README.md) | 목록·생성·설정 화면 |
| [`components/`](./components/README.md) | 진척률, 분석 스택 카드, 저장소·분석 대상 카드, 연결 안내, 프로젝트 없음 상태 |
| [`lib/`](./lib/README.md) | 설정 폼 필드 표, 숫자 칸 규격, 상태 이름, 대상 카드 문구 규칙 |

## 설계 이유

- 프로젝트 목록은 `useProjects` 하나로 조회합니다. 셸과 대부분의 화면이 같은 요청(`/projects`)과 같은 키(`["projects"]`)를 씁니다.
  한 곳이 키를 잘못 적으면 생성·저장·삭제 뒤의 무효화가 그 화면에만 반영되지 않아 이전 역할·이름이 남습니다.
- 조회 옵션은 부르는 곳마다 필요한 키만 넘깁니다. 쓰지 않는 옵션 키를 `undefined`로 넘기면 쿼리 클라이언트의 기본 `staleTime`(30초)을 덮어써서 0이 됩니다.
- 단건 조회(`useProject`)는 검수 화면, 라벨링 워크벤치, 내 작업이 같은 키(`["project", id]`)로 캐시를 나눠 씁니다.
- `index.ts`가 라우트를 내보내지 않는 이유는 import 순환입니다. 셸(`RoleGuard`·사이드바)이 이 파일에서 목록 훅을 가져가고, 라우트는 셸의 가드를 가져옵니다.
- 저장소·분석 자격증명은 프로젝트가 설정에서 고른 연결 하나에서만 읽습니다. 연결을 고르지 않으면 서버가 등록·분석을 409로 막고, 화면은 자격증명 연결 화면으로 가는 방법을 안내합니다.

## 의존성

- 부르는 것: `shared/ui/*`, `shared/api/*`, `shared/lib/*`, `shared/hooks/useUnsavedGuard`·`useDocumentTitle`, `shell/RoleGuard`·`shell/AccessDenied`, `hooks/useAnalysisStack`,
  `store/auth`·`store/project`, `lib/progress`·`role_label`, `features/platform`(`DiagnosticsList`·`diagnosticsSummary`)
- 부르는 쪽: 셸 전반(사이드바·상단바·전환기·가드·개인 탭), `app/redirects.tsx`, 대부분의 기능 화면(`useProjects`), 검수·라벨링 화면(`useProject`),
  통계·개요(`ProjectShellState`·`ConnectionNotice`), 등록(`ConnectionNotice`), 감사 이벤트 표(`FIELD_META`)

## 남은 일

- 여러 화면이 `projects.data?.find((p) => p.slug === slug)`로 주소의 프로젝트를 찾습니다. 같은 식을 훅 하나로 모을 수 있습니다. 부르는 곳마다 조회 옵션이 달라서 지금은 목록 훅만 모았습니다.
- 설정 화면(`pages/project_settings.tsx`)은 모든 설정 페이지를 한 파일에 둡니다. 설정 페이지별 파일로 나눌 수 있습니다.
- `hooks/useAnalysisStack.ts`는 이 기능의 훅이지만 네 기능이 씁니다. 쓰는 곳이 줄면 이 폴더로 옮깁니다.
- 분석 스택 설명 문장(`description`)은 서버(`apps/api-gateway/api_gateway/services/analysis/presets.py`)가 줍니다. 그 문장은 캡션을 쓰는 사람을 `작업자`라고 부릅니다. 용어 표준(`라벨러`)과 맞출지는 서버 쪽에서 정합니다.
