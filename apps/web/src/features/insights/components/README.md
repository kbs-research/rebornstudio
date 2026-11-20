# features/insights/components

## 목적

통계 화면의 사람별 실적 패널과 개요 화면의 역할별 카드를 둡니다.

## 주요 파일

통계 패널(`../pages/insights.tsx`가 그립니다):

| 파일 | 역할 |
|---|---|
| `PeriodSummary.tsx` | 기간 합계 칸 다섯 개(제출·판정·승인율·반려율·수정률). 서버의 기간 합계(`labeler-performance/summary`)를 그리고, 칸 값은 `lib/summary`가 고릅니다. 수는 아래 사람별 표의 행을 더한 값과 같습니다 |
| `LabelerPerformancePanel.tsx` | 사람별 처리량(제출)·승인율·반려율·수정률(모두 분모 함께 표시, 기간 적용). 보유 중과 최근 활동은 멤버 표가 보여 줍니다. 행을 누르면 사람 서랍(`features/people`)을 엽니다 |

개요 카드(`../pages/overview.tsx`가 역할별로 그립니다):

| 파일 | 역할 |
|---|---|
| `useCapacity.ts` | `dashboard/capacity` 조회 훅. 조치 필요가 장기 대기·납품 불가를 읽습니다 |
| `ActionNeeded.tsx` | 관리자의 조치 필요. 비식별화 신고(서버 요약의 `deident_reports`), 반복 반려, 장기 검수 대기 등을 행으로 두고, 행마다 수·분모·이동할 화면을 쓰고, 0건 항목은 아래 한 줄로 모읍니다. 행 계산은 `useActionRows`로 나눠 개요가 배치를 고를 때도 같은 결과를 씁니다 |
| `ManagerCards.tsx` | 진척률 카드(`progressView`, 납품 누적 시간 막대), 단계별 대기(`pipeline/summary`, 개와 건 단위를 나눔). 카드 머리 `CardHead`, 이동 링크 `MoreLink`도 여기 있습니다 |
| `WorkerCards.tsx` | 라벨러·검수자가 이어서 할 일(보유 수와 받을 수 있는 수, `내 작업 열기`) |
| `GuideCard.tsx` | 작업 가이드. 작업자에게는 펼쳐서, 관리자에게는 세 줄로 보입니다. 본문 유무(`hasGuideText`)는 관리자·작업자 개요가 배치를 고를 때 씁니다 |

## 설계 이유

- 패널의 카드 면은 `Card`가 그립니다. 클래스를 손으로 베끼면 `shadow-e1`이 빠져서 같은 화면의 카드가 다른 깊이로 보입니다.
- 통계 카드 머리에는 제목과 기간·분모 배지만 둡니다. 카드마다 다른 장식 아이콘은 아무것도 구별하지 않는데도 뜻이 있는 것처럼 보입니다.
- 실패와 빈 결과를 구분합니다. 분기 순서는 실패, 아직 없음, 모두 비었음, 목록입니다. 값이 없는 상태를 0건 분기와 같은 칸에 두면, 불러오지 못한 상태가 막힌 것이 없다는 문장으로 표시됩니다.
- 사람 단위 수(제출·승인율·반려율·수정률)와 기간 합계에는 순위와 판정 색을 쓰지 않습니다. 승인율의 분모는 판정 수이고 반려율의 분모는 작업량(제출)입니다. 그래서 두 비율의 합은 100%가 아닙니다.
- 조치 필요는 개요에만 둡니다. 통계는 사람별 비교 화면이라 운영 경보와 섞지 않습니다.
- 패널은 주소의 프로젝트를 씁니다. 전역 선택 값은 주소와 맞춰지기 전에 다른 프로젝트를 가리킬 수 있습니다. 그 값을 쓰면 링크로 들어온 첫 요청이 다른 프로젝트로 나갑니다.

## 의존성

- `../api.ts`, `../lib/*`, `shared/ui/*`, `shared/api/client`(`isForbidden`), `lib/progress`·`roles`·`person`·`role_label`, `shell/nav-config`, `store/auth`
- `features/projects`(`ProjectShellState`), `features/people`(`PersonDrawer`), `features/pipeline`(단계 요약·`pipelineKeys`), `features/me`(대기열 호출·`meKeys`)
- 부르는 쪽: `../pages/*`

## 남은 일

- 없음.
