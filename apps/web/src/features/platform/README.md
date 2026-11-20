# features/platform

## 목적

최고관리자의 플랫폼 관리를 담당합니다. 플랫폼 개요(`/admin`), 사용자(`/admin/users`),
자격증명 연결(`/admin/connections`) 화면과 그 화면들이 쓰는 연동 진단 목록·순수 규칙이 여기 있습니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `api.ts` | 서버 호출(마지막 연동 진단 조회, 연결 상태 요약, 계정 생성·역할·상태·재설정 링크·활동 이력, 자격증명 연결 저장과 저장 직후 점검 결과)과 키 팩토리 `platformKeys` |
| `routes.tsx` | 라우트 배열 두 개. `app/router.tsx`가 플랫폼 가드 묶음 안에 `platformRoutes`(사용자·개요·옛 개요 주소 두 개)와 `platformOpsRoutes`(자격증명 연결)를 두 자리에 나눠 펼칩니다 |
| `index.ts` | 밖으로 내보내는 것: `DiagnosticsList`·`diagnosticsSummary`. 화면과 라우트는 내보내지 않습니다 |
| [`pages/`](./pages/README.md) | 플랫폼 화면 세 개 |
| [`components/`](./components/README.md) | 연동 진단 목록 |
| [`lib/`](./lib/README.md) | 연결 서비스 이름, 연결 상태 한 줄 |

## 설계 이유

- `index.ts`는 다른 기능이 쓰는 진단 목록 부품만 내보냅니다. 서버 호출과 쿼리 키(`platformKeys`)는 플랫폼 화면 안에서만 씁니다.
- `index.ts`는 화면을 내보내지 않습니다. 무거운 화면을 다른 기능으로 끌어오지 않게 합니다.
- 저장소·분석 자격증명은 연결 화면에 등록하고, 각 프로젝트가 설정에서 연결 하나를 고릅니다.

## 의존성

- 부르는 것: `shared/ui/*`, `shared/api/*`, `shared/lib/*`, `shared/hooks/useOutsideClose`, `shell/AccessDenied`, `components/StatusBadge`, `store/auth`,
  `lib/role_label`, `features/audit`(`fmtEventTime`·`humanizeEvent`), `features/projects`(`useProjects`, 활동 이력의 프로젝트 이름)
- 부르는 쪽: `app/router.tsx`, `features/projects`(진단 목록)

## 남은 일

- 진단을 실행하는 화면이 하나 더 생기면 실행 버튼과 마지막 실행 시각까지 `components/`로 올립니다. 지금은 프로젝트 설정만 진단을 실행하고 플랫폼 개요는 주기 점검 결과를 읽으므로 그리는 부품만 공유합니다.
