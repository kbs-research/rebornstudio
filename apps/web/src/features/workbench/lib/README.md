# features/workbench/lib

## 목적

워크벤치의 순수 규칙을 둡니다. 캡션 문장과 토큰 규칙, 단축키 표와 처리기, 첫 내용 고르기, 돌아갈 화면, 재작업·보유 판정, 확인 창 잠금이 여기 있습니다.
화면 파일 밖에 두어서 렌더 없이 테스트하고, 캡션 규칙은 서버와 같은 판정 표를 읽어 대조합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `caption.ts` | 캡션 문장 규약. 저장 단위는 문장입니다(`CaptionSentence`). 저장 데이터에서 문장을 읽는 함수는 `sentencesOf`·`finalText` 두 개이고, 세 가지 형식(문장 배열·`caption.brief`·`caption` 문자열)을 모두 읽습니다. 살아 있는 문장(`liveSentences`), 잇기(`joinSentences`), 문자열에서 문장 만들기(`toSentences`)도 있습니다 |
| `caption.test.ts` | `packages/reborn-contracts/caption-shapes.json`을 서버 테스트와 함께 읽어 같은 캡션을 읽는지 확인하는 테스트 |
| `tokens.ts` | 캡션 토큰 수 추정(`estimateTokens`). 한글은 글자 수, ASCII는 단어 수로 셉니다 |
| `tokens.test.ts` | `packages/reborn-contracts/caption-gate.json`을 서버 테스트와 함께 읽어 제출 조건 판정이 같은지 확인하는 테스트 |
| `shortcuts.ts` | 워크벤치 단축키 표. 한 줄이 화면 표기(`keys`·`desc`)와 키 판정(`match`)을 함께 가집니다(재생 `PLAYBACK_SHORTCUTS`, 임시저장·제출 `LABEL_SHORTCUTS`, 문장 편집 `EDIT_SHORTCUTS`). 라벨링 단축키 창의 열(`LABEL_HELP`)과 그 표기 목록(`helpColumns`)도 여기서 만듭니다. 글자 키는 `keyOf`로 읽어 한글 입력 상태에서도 물리 키로 판정합니다. 표기를 키캡으로 나누는 `keysOf`도 있습니다 |
| `shortcuts.test.ts`·`shortcuts_keys.test.ts` | 단축키 창의 줄이 처리기가 읽는 표에서 나오는지, 표기대로 누른 키가 그 줄에 맞는지, 두지 않는 키(건 이동·가이드·문장 고르기·경계 찍기)가 어느 줄에도 맞지 않는지 확인합니다. `keysOf`·`keyOf` 테스트도 있습니다 |
| `useVideoShortcuts.ts` | 공통 영상 단축키 처리기(`useVideoShortcuts`), 재생 도움말 묶음(`VIDEO_SHORTCUTS`), 입력 중 판정(`isTyping`·`ownsActivationKey`) |
| `initial_content.ts` | 워크벤치를 열 때 캡션을 채울 출처를 고릅니다(`pickInitialContent`). 임시저장 초안, 마지막 제출 라벨, AI 초안 순이고, AI 초안은 저장된 라벨이 없다고 확정된 뒤에만 고릅니다. 초안이 없는데 이력 조회가 실패하면 `failed`를 돌려줍니다. 화면을 떠날 때 마지막 초안을 작업 캐시에 넣는 `withSavedDraft`도 있습니다 |
| `home.ts` | 라벨링 워크벤치에서 돌아갈 화면(`labelWorkbenchHome`: 관리자는 작업 단계의 라벨링 화면, 작업자는 내 작업), 읽기 전용 사유(`readOnlyReason`), 작업을 놓은 뒤 돌아갈 화면 안내(`afterReleaseHint`) |
| `hold_lost.ts` | 화면을 보던 사이에 보유가 끝난 작업의 문구와 판정(`isHoldLost`·`lostWhileOpen`). 다른 탭의 신고와 멤버 변경을 구분할 수 없으므로 어느 쪽이든 맞는 문구를 씁니다 |
| `confirm_gate.ts` | 확인 창의 실행을 한 번만 통과시키는 잠금(`createConfirmGate`) |
| `rework.ts` | 지금 재작업 중인지 판정합니다(`isRework`). 마지막 판정과 누적 반려 횟수를 함께 봅니다 |
| `mutation_deps.test.ts` | 훅 의존성 목록에 `useMutation` 결과 객체를 넣은 줄을 `src` 전체에서 찾는 테스트 |

`home`·`hold_lost`·`confirm_gate`·`rework`·`initial_content`는 같은 폴더에 `*.test.ts`가 있습니다.

## 설계 이유

- 캡션이 문자열 하나이면 문장마다 식별자를 둘 수 없습니다. 마침표 하나를 지워 두 문장이 합쳐지면 뒤 문장의 번호와 구간이 모두 밀립니다. 그래서 문장을 개체로 저장합니다.
- 단축키의 처리기와 도움말이 같은 표를 읽으므로 한쪽만 고칠 수 없습니다. 한글 입력 상태에서는 입력된 글자가 영문이 아니므로 물리 키로 판정합니다.
- 작업·라벨 이력·클립은 따로 조회되어 도착 순서가 정해져 있지 않습니다. 그래서 첫 내용 고르기를 순수 함수로 두고 도착 순서마다 테스트합니다.
  클립이 이력보다 먼저 도착했을 때 AI 초안을 먼저 채우면, 나중에 온 라벨로 캡션이 복원되어도 AI 초안 안내와 교정 표시가 남습니다.
- 실행 버튼의 `disabled`는 대기 상태가 다음 렌더에 반영된 뒤에야 걸립니다. 짧은 간격으로 더블클릭하면 요청이 두 번 나가므로 동기 잠금을 둡니다.
- 재투입하면 누적 반려 횟수가 0으로 돌아갑니다. 마지막 판정만 보거나 반려 횟수만 보면 재작업 여부를 잘못 판정합니다.
- `useMutation` 결과 객체는 렌더마다 새로 만들어집니다. 그 객체를 훅 의존성에 넣으면, 매초 다시 그리는 화면에서 30초 자동 저장 타이머가 매초 초기화되어 실행되지 않습니다. `tsc`가 잡지 못하므로 테스트가 소스를 읽어 막습니다.
- 관리자의 돌아갈 화면을 내 작업으로 정하면 그 사람의 일이 없는 화면으로 보내게 됩니다. 그래서 역할별로 돌아갈 화면을 나눕니다.

## 의존성

- `../components/CaptionView.tsx`(`caption.ts`가 `splitSentences`를 씀), `shared/api/error_text`
- 부르는 쪽: `../components/*`, `../pages/*`, `../index.ts`를 거쳐 `features/pipeline`·`features/me`·`features/library`·`components/ReadOnlyLabel`
