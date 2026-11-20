# features/me/components

## 목적

내 작업과 내 활동 화면의 본문을 역할별로 그리는 부품과 두 대기열의 조회·다음 작업 훅을 둡니다. 훅은 작업 단계 화면도 `../index.ts`로 가져갑니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `MyQueues.tsx` | 내 작업 본문. 라벨러(`LabelerQueue`)와 검수자(`ReviewerQueue`)에게 다음 작업 카드(`features/pipeline`의 `NextWorkCard`) 하나를 두고, 보유한 작업이 있으면 캡션 미리보기와 재작업의 마지막 반려 사유를 붙입니다. 관리자(`AdminQueue`)는 두 대기열을 세그먼트로 전환합니다 |
| `QueueParts.tsx` | 보유 작업 미리보기 부품: 클립 한 줄(`ClipLine`, 영상 제목·구간과 캡션 미리보기), 반려 사유(`RejectReasons`) |
| `useMyQueues.ts` | 두 대기열 훅(`useLabelQueue`·`useReviewQueue`)과 워크벤치 주소(`workbenchPath`). 대기 수·보유 수는 서버가 센 값을 쓰고, 다음 작업으로 받은 작업의 워크벤치를 바로 엽니다 |
| `MyActivity.tsx` | 내 활동 본문 `ActivityBody`. 라벨러·관리자에게는 내 라벨링 기록 표와 기록 상세 서랍(제출 라벨은 `components/ReadOnlyLabel`)을 보이고, 검수자에게는 내 판정 목록을 보입니다. 수·비율 같은 지표는 두지 않습니다 |

## 설계 이유

- 한 사람은 역할마다 한 건씩 맡습니다. 화면의 할 일은 다음 작업 버튼 하나이고, 서버가 보유한 작업을 먼저 다시 주거나 한 건을 배정합니다. 고를 수 있는 목록, 반납, 남은 시간, 한도는 두지 않습니다.
- 조회 키는 작업 단계 화면의 대기열과 같습니다. 두 화면이 같은 캐시를 읽으므로 한쪽에서 다음 작업을 받으면 다른 쪽에도 같은 수가 보입니다.
- 대기 수·보유 수는 서버가 센 값을 씁니다. 재작업이 돌아오면 보유 수가 2일 수 있고, 다음 작업이 재작업을 먼저 엽니다.
- 카드 모양과 낱말(`NextWorkCard`·`QUEUE_SPEC`)은 작업 단계 화면과 함께 씁니다. 미리보기만 다릅니다. 내 작업은 캡션 미리보기와 반려 사유를, 작업 단계 화면은 클립 칩을 씁니다.
- 미리보기에는 작업 ID를 두지 않습니다. 작업자는 해시만 보고 작업을 떠올리지 못합니다. 클립 줄의 영상 제목·구간·요약으로 작업을 알아봅니다. 작업 ID는 내 활동의 기록 표와 상세에 있습니다.
- 내 활동 기록 표의 검수 결과 칸에는 상태 칸이 보여 주지 않는 판정만 씁니다(`../lib/my_history.ts`의 `verdictAddsInfo`).

## 의존성

- `../api.ts`, `../lib/*`, `shared/ui/*`, `components/ReadOnlyLabel`, `lib/anon`·`lib/vocab`
- `features/pipeline`(`NextWorkCard`·`nextErrorText`·`poolFailed`·`pipelineKeys`), `features/library`(`fetchClip`·`libraryKeys`·`SPEECH_SOURCE_LABEL`), `features/projects`(`useProjects`)
- 부르는 쪽: `../pages/inbox.tsx`·`../pages/my_activity.tsx`

## 남은 일

- 없음.
