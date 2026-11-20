# features/people

## 목적

프로젝트 멤버 기능을 담당합니다. 멤버 화면(`/p/:slug/people`)과 멤버 관리(초대·역할·제거)가 여기 있습니다.
한 사람의 실적과 설정을 보여 주는 사람 서랍도 이 폴더에 둡니다.
멤버(이 프로젝트에 속한 사람)와 사용자(플랫폼 계정)는 다릅니다. 계정은 `features/platform`의 사용자 화면이 담당합니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `api.ts` | 멤버 경로(`/projects/{id}/members/*`)의 서버 호출(목록·초대·역할·제거)과 키 팩토리 `peopleKeys` |
| `routes.tsx` | `people` 가드 아래의 멤버 화면 라우트 `peopleRoutes` |
| `index.ts` | 밖으로 내보내는 것: 사람 서랍 `PersonDrawer`(통계 사람 표), 멤버 목록 `listMembers`·`peopleKeys`(감사 로그) |
| [`pages/`](./pages/README.md) | 멤버 화면 |
| [`components/`](./components/README.md) | 멤버 표, 사람 서랍, 설정 탭, 확인 창 |
| [`lib/`](./lib/README.md) | 프로젝트 역할 목록, 멤버십 상태 이름 |

## 설계 이유

- 이 화면을 여는 사람과 초대·역할·제거를 정하는 사람은 모두 프로젝트 관리자입니다.
- 멤버는 관리자의 초대로만 들어옵니다. 참여 요청과 승인, 참여 기간은 없습니다. 서버도 같은 가드(`require_project_admin`)로 막습니다.
- 표는 읽기 전용이고, 편집은 사람 서랍의 설정 탭 한 곳에서 합니다. 행 안에 편집 컨트롤을 두면 한 사람을 고치려고 여러 곳을 열어야 합니다.
- 도구는 사람에게 품질 등급을 매기지 않습니다. 멤버 화면에 등급 탭을 두지 않습니다. 근무 기록과 판정 기록 탭도 두지 않습니다. 로그인 이력은 본인만 내 계정에서 봅니다. 작업자별 수치는 통계의 작업량 대비 반려 화면에서 사람이 보고 판단합니다.
- 이 기능의 `api.ts`에는 멤버 경로만 둡니다. 사람 서랍이 쓰는 통계 조회는 `features/insights`의 키·호출로 부릅니다.
  같은 키를 만드는 팩토리가 두 개이면 한쪽의 무효화가 다른 쪽에 반영되지 않습니다.

## 의존성

- 부르는 것: `shared/ui/*`, `shared/api/*`, `shared/lib/josa`, `shell/RoleGuard`·`shell/nav-config`(`INSIGHTS_ROLES`), `components/StatusBadge`, `store/auth`,
  `lib/role_label`·`person`·`vocab`, `features/insights`(통계 조회·키·표기), `features/projects`(`ProjectShellState`·`useProjects`·`projectKeys`)
- 부르는 쪽: `app/router.tsx`, `features/insights`(사람 표)

## 남은 일

- `lib/roles.ts`의 `ProjectRole`과 `src/lib/roles.ts`의 `ProjectRole`은 이름과 값이 같지만 출처가 다릅니다(목록 상수와 DTO). 합치려면 부르는 곳의 타입까지 확인해야 합니다.
