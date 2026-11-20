# features/insights/pages

## 목적

개요와 통계 화면을 둡니다. 주소 연결은 `../routes.tsx`가 합니다. 패널과 카드는 라우트가 아니라 이 두 화면이 끼워 쓰는 부품입니다.

## 주요 파일

| 파일 | 경로 | 역할 |
|---|---|---|
| `overview.tsx` | `/p/:slug/overview` | 개요 `OverviewPage`. 관리자에게는 조치 필요·진척률·작업 가이드·단계별 대기를 그리고, 카드 배치는 조치 필요 행 수와 작업 가이드 유무로 고릅니다(`lib/action_rows`의 `overviewLayout`). 라벨러·검수자에게는 이어서 할 작업과 작업 가이드를 그립니다. 작업 결과를 볼 수 있는 검수자에게는 진척률을 이어서 할 작업 옆에 두고, 작업 가이드가 있으면 그 아래 전체 폭에 둡니다(`lib/action_rows`의 `workerOverviewLayout`). |
| `insights.tsx` | `/p/:slug/insights` | 통계 `InsightsPage`. 머리의 기간 선택, 기간 합계 칸(제출·판정·승인율·반려율·수정률), 사람별 실적 표를 둡니다. 관리자만 엽니다. |

## 설계 이유

- 통계는 프로젝트 전체의 기간 합계와 여러 사람을 같은 기준으로 나란히 보는 표를 둡니다. 진척·대기처럼 지금 할 일을 정하는 수는 개요에 둡니다.
- 기간 선택은 페이지 머리에 하나만 둡니다. 합계 칸과 사람별 표가 같은 주소 값(`?period=`)을 읽으므로 위아래 수가 늘 같은 기간입니다.

## 의존성

- `../components/*`, `../lib/*`, `shared/ui/*`(`PageHeader` 등), `shell/RoleGuard`, `lib/roles`, `features/projects`

## 남은 일

- 없음.
