# features/projects/lib

## 목적

프로젝트 설정과 대상 카드의 순수 규칙을 둡니다. 화면 파일 밖에 두어서 렌더 없이 테스트하고, 서버 계약과 같은 값인지 테스트로 대조합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `settings_form.ts` | 설정 폼의 필드 표 `FIELD_META`(필드마다 설정 페이지·이름·단위·설명), 메뉴 묶음 `PAGE_GROUPS`, 예전 탭 주소와 없어진 페이지 주소(`review`·`analysis`)를 지금 페이지로 옮기는 `pageFromParam`, 저장 막대의 변경 목록(`changeRows`·`changeList`·`changeSummary`), 바뀐 필드만 담는 저장 본문 `patchBody` |
| `settings_form.test.ts` | 모든 필드가 설정 페이지에 속하는지, 메뉴의 변경 표시와 저장 막대 목록이 맞는지, 저장 본문이 계약(`ProjectUpdateRequest`·`CaptionQcInput`)에 있는 필드만 싣는지 확인하는 테스트 |
| `settings_numbers.ts` | 숫자 칸의 규격(`NUMBER_SPECS`: `min`·`max`·정수 여부·비울 수 있는지), 저장 전 검사(`checkNumber`), 서버 422가 가리키는 범위 문구(`boundsText`) |
| `settings_numbers.test.ts` | 규격이 `docs/api/openapi.json`의 범위·정수 여부와 같은지 확인하는 테스트. `openapi.json`을 상대 경로로 읽으므로 폴더를 옮기면 경로도 고쳐야 합니다 |
| `project_status.ts` | 프로젝트 상태의 화면 이름(`PROJECT_STATUS_LABEL`)과 순서. 목록 배지와 설정 선택지가 씁니다 |
| `stack_target.ts` | 대상 카드의 문구 규칙과 일시 오류 재조회 규칙. 잠긴 이유의 제목(`lockTitle`)에는 서버가 준 영상 수와 그중 지운 수로 분모를 밝힙니다. 재조회는 `TRANSIENT_RETRY_MS`(15초) 간격으로 `TRANSIENT_RETRY_MAX`(4회)까지 합니다 |
| `stack_target.test.ts` | `stack_target.ts` 규칙 테스트 |

## 설계 이유

- 폼에 필드를 더할 때 이 표에 적지 않으면 타입 검사가 실패합니다. 메뉴의 변경 표시와 저장 막대가 같은 표에서 나오므로 두 곳이 서로 달라지지 않습니다.
- 처리 옵션(캡션 최소 길이·반려 사유·클립 길이·장면 요약 다듬기·문장별 시각 표시·원본 영상 보관·비식별화 목표 비율·품질검수 사전과 프롬프트)은 모든 프로젝트가 같은 기본값을 씁니다. 서버는 그 필드를 받으면 422로 거절하므로 폼에도 저장 본문에도 두지 않습니다.
  저장 본문의 타입은 계약의 요청 모델이라 그런 필드를 넣으면 타입 검사가 실패하고, 테스트가 `openapi.json`의 속성 목록과 한 번 더 대조합니다.
- 숫자 칸의 규격을 입력란마다 JSX 리터럴로 흩어 두면 정수 여부를 정한 곳이 없게 됩니다. 그러면 소수를 입력했을 때 범위 문구로 잘못 안내하고, 비울 수 없는 값을 비워도 저장된 것처럼 보입니다.
  범위는 숫자 값이라 `tsc`가 잡지 못하므로 테스트가 계약과 대조합니다.
- 계약의 상태 값 네 개를 모두 적습니다. 하나라도 빠지면 그 상태의 프로젝트가 목록에서 빈 칩으로 그려집니다.
- 영상 목록과 작업 단계는 지운 영상을 세지 않습니다. 잠긴 이유에 영상 수만 쓰면 같은 화면의 영상 수와 어긋나므로 지운 수를 함께 밝힙니다.

## 의존성

- `@reborn/contracts`(`project_status.ts`)
- 부르는 쪽: `../pages/project_settings.tsx`·`project_new.tsx`·`projects.tsx`, `../components/*`, `../index.ts`를 거쳐 `features/audit`(`FIELD_META`)

## 남은 일

- 없음.
