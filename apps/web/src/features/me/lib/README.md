# features/me/lib

## 목적

개인 작업공간의 순수 규칙을 둡니다. 화면 파일 밖에 두어서 렌더 없이 테스트합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `holding.ts` | 보유를 바꾼 뒤(제출·판정·신고) 내 작업의 보유 목록과 보유 수(대기 수 응답)를 함께 무효화하는 `invalidateHolding`. 라벨링 워크벤치와 검수 화면이 `../index.ts`에서 가져갑니다 |
| `holding.test.ts` | 보유를 바꾸는 호출을 하는 뮤테이션이 성공 처리에서 보유 수까지 무효화하는지 소스를 읽어 검사하는 테스트 |
| `queue_preview.ts` | 내 작업 목록의 클립 줄 아래에 보일 캡션 미리보기를 고릅니다(`queuePreview`). 라벨러 캡션(임시저장, 없으면 최신 제출)을 먼저 쓰고, 없을 때만 분석 결과로 대신하며 출처를 함께 돌려줍니다 |
| `queue_preview.test.ts` | 미리보기 선택 순서 테스트 |
| `my_history.ts` | 내 라벨링 기록의 상태 이름·톤(`MY_STATE`), 상태 필터 선택지, 판정 톤, 대상 이름(`영상 제목 · 구간`)과 행 수 표기, 검수 결과 칸에 판정을 쓸지 여부(`verdictAddsInfo`). 내 활동 표·기록 상세가 함께 씁니다 |
| `my_history.test.ts` | 이름·상태 필터·행 수 문구·판정 표시 규칙 테스트 |
| `decisions.ts` | 내 판정 목록의 표기. 판정 이름(`lib/vocab`)과 색조(`decisionView`), 대상(`decisionTarget`, 영상 제목·구간 또는 영상 전체), 수 문구(`decisionCountLabel`) |
| `decisions.test.ts` | 판정 표기 테스트 |
| `login_lock.ts` | 내 계정 로그인 기록의 로그인 잠금 줄 보조 문구(`lockDetail`, 예: `로그인 실패 50회 · 15분 잠금`). 서버가 실은 경로·실패 횟수·잠금 시간만 옮겨 적습니다 |
| `login_lock.test.ts` | 보조 문구 테스트 |

## 설계 이유

- 내 작업은 보유 목록과 보유 수를 서로 다른 조회로 읽습니다. 목록만 무효화하면 목록은 비었는데도 머리의 보유 수가 캐시 유지 시간(30초) 동안 남습니다.
  이 누락은 `tsc`도 빌드도 잡지 못하므로 `holding.test.ts`가 소스를 읽어 막습니다.
- 기록의 상태는 서버가 정합니다(`my_state`). 화면은 이름과 톤만 붙이고 상태를 다시 판정하지 않습니다. 작업의 현재 상태를 그리면 다른 라벨러에게 넘어간 작업이 이 사람의 상태로 보입니다.
- 검수자는 검수할 글을, 라벨러는 자기가 고친 글을 목록에서 알아볼 수 있어야 합니다. 그래서 미리보기는 라벨러 캡션을 먼저 씁니다.
- 판정 표기는 서버가 준 판정 값에 이름과 색조만 붙입니다. 도구는 판정이 좋은지 나쁜지 평가하지 않으므로 수·비율 같은 지표를 만들지 않습니다.

## 의존성

- `../api.ts`(`meKeys`), `features/library`(`clipSummary`)·`features/workbench`(`finalText`)는 `queue_preview.ts`가, `lib/vocab`·`shared/lib/fmtSecs`는 `my_history.ts`·`decisions.ts`가 씁니다
- 부르는 쪽: `../components/*`, `../pages/*`(`login_lock.ts`는 `account.tsx`), `../index.ts`

## 남은 일

- 없음.
