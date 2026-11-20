# features/insights

## 목적

프로젝트의 진행 상황을 보여 주는 기능입니다. 개요(`/p/:slug/overview`)와 통계(`/p/:slug/insights`) 두 화면이 여기 있습니다.
통계의 기간 합계 칸·사람별 실적 패널과 개요의 역할별 카드, 두 화면이 읽는 서버 호출과 쿼리 키, 지표 사전·수정률 같은 통계 규칙도 이 폴더에 둡니다. 통계는 관리자만 엽니다.
개요는 역할마다 첫 화면이 다릅니다. 관리자는 조치 필요와 흐름을, 라벨러·검수자는 이어서 할 작업을 봅니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `api.ts` | 서버 호출(사업 KPI·상태별 작업 수·장기 대기·사람별 실적·기간 합계)과 키 팩토리 `insightsKeys` |
| `routes.tsx` | 라우트 두 묶음. `app/router.tsx`가 개요(`overviewRoutes`)는 일반 멤버 라우트에, 통계(`insightsRoutes`)는 통계 가드(`insights`) 묶음으로 펼칩니다 |
| `index.ts` | 밖으로 내보내는 것: 실적 조회(`fetchLabelerPerformance`)와 `insightsKeys`, 날짜 표기, 수정률 칸, 지표 사전(`METRICS`), 승인율·반려율 칸(`approvalView`·`rejectionView`·`splitUnit`), `doesLabeling`. 화면은 내보내지 않습니다 |
| [`pages/`](./pages/README.md) | 개요와 통계 화면 |
| [`components/`](./components/README.md) | 통계 패널과 개요 카드 |
| [`lib/`](./lib/README.md) | 지표 사전, 수정률, 날짜 표기, 조치 필요 행 판정 |

## 설계 이유

- 쿼리 키는 `insightsKeys`로만 만듭니다. 개요 카드 여럿이 같은 조회를 같은 키로 불러서 요청이 한 번만 나갑니다. 사람 서랍·멤버 표도 통계의 사람 표와 같은 키로 캐시를 나눠 씁니다.
  조회 옵션(갱신 주기)은 쓰는 곳마다 다르게 둡니다. 옵션까지 한 훅으로 모으면 한쪽의 갱신 주기가 바뀝니다.
- 개요 카드가 다른 기능의 조회를 쓰는 곳(작업 단계 요약, 대기열)은 그 기능의 호출과 키를 `index.ts`에서 가져옵니다. 내 작업 화면과 같은 요청, 같은 키입니다.
- `index.ts`가 화면을 내보내지 않는 이유는 import 순환입니다. 통계의 사람 표가 사람 서랍(`features/people`)을 열고, 사람 서랍은 이 기능의 표기·조회를 `index.ts`에서 가져갑니다.
- 수는 모두 서버가 셉니다. 진척률 분기는 관리자·검수자 개요가 함께 쓰는 `lib/progress.ts`의 `progressView`가 정합니다. 단계별 대기는 사이드바 배지와 같은 `pipeline/summary`를, 납품 가능 수는 서버 `business-kpi`의 `deliverable`을 씁니다.
- 분모가 0이면 판정하지 않습니다. 작업이 0건일 때 승인율을 0으로 받아 색을 칠하면, 양호나 경고라는 없는 사실을 보이게 됩니다. 서버는 `None`을 주고, 화면은 `—`(제출도 없음)나 `측정 전`(제출은 있는데 판정 없음)으로 그립니다. 비율 아래 괄호의 분모도 서버가 준 값(`approval_base`·`rejection_base`)을 씁니다.
  진척률도 작업이 0건이면 `0%`를 그리지 않습니다.
- 조회 실패를 빈 결과로 그리지 않습니다. 403이면 카드를 그리지 않습니다(`isForbidden`). 그 밖의 실패에서 보여 줄 값이 없으면 `ErrorState inline`과 다시 시도를 그리고, 이전 값이 있으면 그 값을 그대로 둡니다.
  주소의 프로젝트를 찾지 못한 경우도 조회 실패와 구분합니다(`features/projects`의 `ProjectShellState`).
- 기간이 걸리는 수(제출·승인·반려 건수)와 현재 상태를 나타내는 수(큐 깊이·진행률)를 나눕니다. 상태 기반 카드는 기간을 좁혀도 값이 바뀌지 않으므로 기간 컨트롤을 달지 않습니다.
  근거는 `shared/ui/PeriodSelect.tsx` docstring에 있습니다.

## 의존성

- 부르는 것: `shared/ui/*`(`Card`·`PeriodSelect`·`TableCard` 등), `shared/api/client`, `shared/lib/*`, `shell/RoleGuard`·`nav-config`,
  `store/auth`, `lib/progress`·`roles`·`role_label`·`person`, `features/projects`, `features/people`(`PersonDrawer`), `features/pipeline`, `features/me`
- 부르는 쪽: `app/router.tsx`, `features/people`(사람 서랍·멤버 표·사람 설정·멤버 화면)

## 남은 일

- `lib/dates.ts`는 도메인과 무관한 날짜 표기입니다. `shared/lib/`로 옮기면 기능 폴더 사이의 의존이 하나 줄어듭니다.
- 사람별 실적 조회는 기간 조각이 템플릿 안의 삼항 연산자로 되어 있습니다. 그래서 `scripts/ci/check-missing-routes.mjs`가 경로를 계산하지 못하는 호출로 적어 둡니다. 그 경로는 서버(`api_gateway/api/v1/dashboard.py`)에 있습니다.
