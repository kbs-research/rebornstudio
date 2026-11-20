# features/workbench/components

## 목적

라벨링 워크벤치(뷰 `LabelWorkbench`와 로직 훅 `useLabelWorkbench`)와 워크벤치 공용 부품을 둡니다. 공용 부품은 검수 워크벤치, 클립 보기, 완료 보기가 `../index.ts`에서 가져갑니다.

## 주요 파일

라벨링 워크벤치:

| 파일 | 역할 |
|---|---|
| `useLabelWorkbench.ts` | 라벨링 워크벤치의 로직. 작업·클립 로딩, 캡션 편집 상태, 자동 임시저장(바뀐 내용이 있으면 30초 뒤), 제출(확인 창 경유)·신고와 그 뒤 돌아갈 화면으로 이동, 단축키, 편집 잠금 조건(`locked`)과 제출 조건을 한 객체로 돌려줍니다. 주소의 `id`를 직접 읽습니다 |
| `LabelWorkbench.tsx` | 라벨링 워크벤치 뷰. 머리 줄, 왼쪽 무대(영상), 오른쪽 416px 레일(반려 카드·캡션·영상 메타)과 레일 바닥의 제출 전 확인을 둡니다. 훅의 값을 받아 그리기만 합니다 |
| `label_parts.tsx` | 라벨링 레일의 표시 부품: 캡션 글자 크기(`CAPTION_FONT_SIZE`), 절(`WbSection`), 캡션 길이 단위 설명(`CAPTION_LENGTH_UNIT`), 저장 상태(`WbSaveStatus`), 영상 메타 읽기 전용 절(`WbVideoMeta`), 라벨링 단축키 창(`WbShortcutsModal`), 절 배지(`SECTION_BADGE`). 검수 워크벤치도 일부를 씁니다 |
| `SentenceEditor.tsx` | 캡션을 문장 카드 단위로 씁니다. 문장 수정·삭제와 끝에 추가를 지원합니다. 삭제한 문장은 바로 빼지 않고 삭제됨 표시로 남겨 되돌릴 수 있게 합니다 |
| `ReportDeidentModal.tsx` | 비식별화 미흡 신고 창. 신고하면 작업의 보유가 풀리고 초안이 지워진다는 사실과, 관리자가 재개할 때까지 작업이 멈춘다는 사실을 먼저 씁니다. 라벨링·검수 워크벤치가 씁니다 |

워크벤치 공용 부품:

| 파일 | 역할 |
|---|---|
| `WorkbenchHeader.tsx` | 워크벤치 머리 줄(56px 한 줄). 뒤로 가기, 제목 두 줄, 수 묶음(반려 횟수), 저장 상태, 도구, 임시저장, 주 버튼을 둡니다. 열려 있는 동안 셸 영역을 `inert`로 만듭니다 |
| `WorkbenchConfirm.tsx` | 되돌릴 수 없는 액션(라벨링 제출)의 확인 창. 굵은 결과, 이름·값 표, 경고 줄, 실행 뒤 열리는 화면을 둡니다. 포커스는 취소 버튼에 두고, `⌘/Ctrl+Enter`로 실행합니다. 실행은 `../lib/confirm_gate.ts`로 한 번만 들어갑니다 |
| `GuidePanel.tsx` | 작업 가이드 서랍. 배경막 없이 레일 위에 겹쳐서 영상과 재생 단축키를 가리지 않습니다. 머리 줄의 `가이드` 버튼으로 여닫고 Esc로 닫습니다 |
| `WbShortcutsDialog.tsx` | 워크벤치 단축키 창. 왼쪽에는 재생 키를, 오른쪽에는 그 워크벤치의 키를 키캡으로 그립니다. 열이 하나면 한 열로 그립니다 |
| `Keycaps.tsx` | 키캡 묶음. 키 하나가 캡 하나입니다 |
| `CaptionView.tsx` | 긴 캡션·장면 요약을 문장 단위로 나눠 읽기 좋게 그립니다(`CaptionBody`). 문장 나누기 규칙 `splitSentences`도 여기 있습니다 |
| `SentenceTriage.tsx` | 검수용 문장 판정(확인·수정·삭제·반려). 자동 검수 지적이 해당 문장 아래에 붙습니다 |
| `chip.ts` | 워크벤치 칩 크기 기준(`WB_CHIP`·`WB_CHIP_TIGHT`)과 반려 사유 칩(`REASON_CHIP`). 색은 부르는 쪽이 붙입니다 |

## 설계 이유

- 로직과 뷰를 나눕니다. 훅이 상태와 판정을 가지고, 뷰는 그리기만 합니다. 편집 잠금(`locked`)은 버튼뿐 아니라 전역 키 처리기와 `SentenceEditor`의 `readOnly`도 같은 값을 봅니다.
- 제출은 확인 창을 거칩니다. 라벨링 제출은 되돌릴 방법이 없습니다(검수로 넘어가고 제출 취소가 없음). 그래서 그 건의 판단 근거(문장 수·캡션 길이·재작업이면 고치지 않은 지적 문장)를 같은 창에 둡니다.
- 문장 카드의 조작 도구는 지금 편집하는 행에서만 보이고, 숨긴 동안에는 `pointer-events`도 끊습니다. 보이지 않는 삭제 버튼이 눌리지 않게 하기 위해서입니다.
- 제출·신고 뒤에는 돌아갈 화면(작업자는 내 작업)으로 갑니다. 다음 작업은 그 화면의 버튼 하나가 서버가 고른 건을 엽니다. 반납과 보유 시간은 없습니다.
- 칩 크기를 부르는 곳마다 적으면 같은 뜻의 표식이 파일마다 다른 크기가 됩니다. 그래서 `chip.ts` 한 곳에 둡니다.

## 의존성

- `../api.ts`, `../lib/*`, `../store.ts`, `shared/ui/*`, `shared/hooks/*`, `shared/lib/*`, `hooks/useSlugProject`, `lib/*`(메타·역할·익명화), `components/StatusBadge`
- `features/library`(`VideoPlayer`·클립 조회), `features/me`(보유 무효화), `features/pipeline`(`pipelineKeys`), `features/projects`(`useProject`)
- 부르는 쪽: `../routes.tsx`(`LabelWorkbench`), `../pages/completed_workbench.tsx`, `../index.ts`를 거쳐 `features/pipeline`·`features/library`·`components/ReadOnlyLabel`
