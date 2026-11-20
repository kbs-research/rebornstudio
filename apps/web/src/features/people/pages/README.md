# features/people/pages

## 목적

멤버 기능의 라우트 화면을 둡니다. 주소 연결은 `../routes.tsx`가 합니다.

## 주요 파일

| 파일 | 경로 | 역할 |
|---|---|---|
| `people.tsx` | `/p/:slug/people` | 멤버 화면 `WorkforcePage`. 머리(제목·요약 한 줄·멤버 초대)만 그리고, 본문은 `../components/MembersPanel.tsx`가 그립니다. |

## 설계 이유

- 품질 등급 탭은 두지 않습니다. 작업자별 수치는 통계의 작업량 대비 반려 화면에서 사람이 보고 판단합니다.

## 의존성

- `../api.ts`(`listMembers`·`peopleKeys`), `../components/MembersPanel.tsx`, `shared/ui/*`, `features/projects`(`ProjectShellState`·`useProjects`)

## 남은 일

- 없음.
