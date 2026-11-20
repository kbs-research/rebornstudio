# features/insights/lib

## 목적

통계와 개요의 순수 규칙을 둡니다. 지표 이름과 정의, 승인율·반려율·수정률 칸, 날짜 표기, 조치 필요 행 판정이 여기 있습니다.
비율·분모 계산은 서버가 하고, 이 폴더는 받은 수를 어떻게 부르고 그릴지만 정합니다. 화면 파일 밖에 두어서 렌더 없이 테스트합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `metrics.ts` | 지표 사전 `METRICS`(사람 단위 지표의 이름·뜻·계산식·단위·표기 예), 정의 문구 `metricDefine`, 분자·분모 표기(`fmtFraction`, `(2/6)` 형식). 이름은 용어 사전을 따릅니다 |
| `edit_rate.ts` | 수정률 칸에 그릴 내용(`editRateView`: 값과 측정 건수, `측정 전`, `—`)과 정의 문구, 100%를 넘는 값의 설명(`EDIT_RATE_OVER_NOTE`·`editRateOver`), 분자·분모(`editRateFraction`: `(교정/초안자)`)와 값의 설명(`editRateNote`). 정의상 100%를 넘을 수 있어 값을 자르지 않고 두 수를 같이 보여 줍니다 |
| `rates.ts` | 승인율·반려율 칸에 그릴 내용(`approvalView`·`rejectionView`: 값과 분자·분모 괄호, `측정 전`, `—`), 반려율(0~1)을 퍼센트로 바꾸는 `rejectionPct`, 값 끝의 `%`를 떼는 `splitUnit`. 괄호의 분모는 서버가 준 `approval_base`·`rejection_base`이고, 사람 표와 서랍이 같은 함수를 씁니다 |
| `summary.ts` | 통계 기간 합계 칸에 그릴 내용(`summaryCells`: 이름·값·단위·아래 줄·정의 문구). 비율 칸은 사람 표와 같은 `approvalView`·`rejectionView`·`editRateView`로 고릅니다 |
| `dates.ts` | 통계·멤버 화면의 날짜·시각 표기(`fmtDay`·`fmtTime`). `shared/lib/time`의 규칙(24시간제, 올해이면 연도 생략)을 따릅니다 |
| `labeling_roles.ts` | 사람별 라벨링 표에 기본으로 싣는 사람(`LABELING_ROLES`: 라벨러·관리자, 그리고 기간 안에 제출했거나 판정받은 사람)과 판정 함수 `doesLabeling`·`labelingRows` |
| `action_rows.ts` | 개요 조치 필요 행의 판정과 문장(`splitActionRows`·`noneLine`·`blockerHint`), 관리자 개요의 카드 배치(`overviewLayout`)와 라벨러·검수자 개요의 카드 배치(`workerOverviewLayout`). 두 배치 모두 어느 열의 아래도 크게 비지 않게 고릅니다 |

`*.test.ts`는 같은 이름 파일의 테스트입니다. `rates.test.ts`는 서버 테스트와 같은 판정 표(`packages/reborn-contracts/labeler-rates.json`)를 읽습니다.

## 설계 이유

- 같은 지표를 화면마다 다른 이름과 표기로 쓰면 두 화면의 수가 같은 값인지 확인할 수 없습니다. 그래서 통계·멤버·개요 화면이 이름과 정의를 `metrics.ts`에서 가져옵니다.
- 수정률의 비율과 분모는 서버(`analytics.dashboard._edit_rate`)가 계산합니다. 측정한 제출이 없으면 `측정 전`이라고 쓰고 0으로 그리지 않습니다.
- 승인율·반려율의 비율과 분모도 서버(`analytics.dashboard._rate_fields`)가 정합니다. 반려율은 판정이 제출보다 많으면 판정 수로 나누므로, 괄호를 제출 수로 쓰면 `100% (18/0)`처럼 비율과 다른 수가 나옵니다. 그래서 괄호는 서버가 준 분모만 씁니다. 제출은 있는데 판정이 없으면 승인율은 `측정 전`입니다.
- 날짜를 감사 로그와 다른 형식으로 쓰면 같은 기록인지 대조할 수 없습니다. 그래서 `dates.ts`는 앱 공용 규칙을 그대로 씁니다.

## 의존성

- `shared/lib/time`(`dates.ts`)
- 부르는 쪽: `../components/*`, `../pages/*`, `../index.ts`를 거쳐 `features/people`

## 남은 일

- 없음.
