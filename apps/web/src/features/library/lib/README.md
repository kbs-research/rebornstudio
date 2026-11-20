# features/library/lib

## 목적

영상과 클립 한 건을 표기하는 규칙과 재생기 규칙을 둡니다. 판정은 서버가 하고, 이 폴더는 이름·색조·문장을 붙입니다. 화면 파일 밖에 두어서 렌더 없이 테스트합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `clip_summary.ts` | 클립 한 줄 요약(`clipSummary`). 장면 요약을 먼저 쓰고, 없으면 `음성 인식` 출처(`SPEECH_SOURCE_LABEL`)를 붙인 발화 문장을 씁니다 |
| `clip_range.ts` | 한 목록 안의 클립 구간을 같은 형식으로 씁니다(`clipRangeFormatter`). 목록에서 가장 늦은 끝 시각이 1시간 미만이면 `m:ss.s`, 1시간 이상이면 `h:mm:ss` 형식입니다 |
| `clip_status.ts` | 클립 행 상태 이름: 작업 상태(`WORK_LABEL`), 작업 티켓이 없는 클립의 클립 상태(`legal_review`는 `비식별화 신고`, `rejected`는 `제외`). 판정은 서버 값(`work_status`·`rights_status`)을 씁니다 |
| `video_stage.ts` | 준비 단계 칩, 막힌 영상의 빈 상태 문구와 다음 할 일. 이름과 색조는 `features/ingest`의 사전을 씁니다 |
| `prep_summary.ts` | 끝난 준비 진행 요약의 표시 규칙. 단계 칩의 표시(`summaryMark`: 확인 필요·건너뜀·완료)와 칩 아래 안내 줄(`summaryNotes`: 확인 필요를 먼저, 건너뜀 사유를 뒤에, 사유 없는 건너뜀은 줄 없음). 상태와 사유 문장은 서버 값을 그대로 씁니다 |
| `prep_poll.ts` | 영상 상세의 다시 읽기 간격(`prepPoll`). 업로드·규격 검증이 진행 중이면 타임라인 3초·상세 5초, 그 뒤 단계만 남으면 10초·15초 간격이고, 모두 끝나면 멈춥니다. 단계 상태 문자열(`stageSignature`)이 바뀌면 영상·클립을 바로 다시 읽습니다 |
| `player_error.ts` | 재생기 오류 화면의 제목·원인·다시 시도 여부. 다시 해도 같은 결과로 실패하는 오류(403·404·409)에는 다시 시도를 주지 않습니다. 원본을 받을 수 없는 역할(라벨러)의 재생본 없음(`invalid_source_uri`)은 재생본 준비 중으로 쓰고 다시 시도를 줍니다 |
| `player_source.ts` | 재생기를 다시 만들 기준 키(`playerKey`, 구간이 바뀌면 새 재생기), 재생 주소가 바뀐 뒤 돌아갈 위치(`resumeSeek`), 미디어 오류가 났을 때 할 일(`mediaErrorAction`) |
| `speaker_spans.ts` | 화자별 발화 구간(처음 시작부터 마지막 끝까지)을 초 단위로 모읍니다 |

파일마다 같은 폴더에 `*.test.ts`가 있습니다.

## 설계 이유

- 영상 상세의 클립 목록·클립 보기·클립 탭이 클립 요약·구간·상태 이름을 각자 정하면 같은 클립이 화면마다 다르게 보입니다.
- 머리 칩과 타임라인이 서로 다른 단계를 보이지 않도록, 단계가 바뀌면 관련 조회를 함께 다시 읽습니다.
- 재생 주소(presigned URL)는 주기적으로 바뀝니다. 주소가 바뀌어 재생기를 다시 만들 때 위치를 0으로 돌리지 않습니다. 구간 정보가 늦게 도착해서 영상 전체가 재생되는 일을 막으려고 키를 구간에 묶습니다.

## 의존성

- `features/ingest`(준비 단계 사전·타임라인 타입), `shared/api/client`(타입)·`shared/api/error_text`(`errorText`·`isRetryable`), `shared/ui/Chip`(색조 타입)
- 부르는 쪽: `../components/*`, `../pages/*`, `../index.ts`를 거쳐 `features/me`·`features/pipeline`·`features/workbench`
