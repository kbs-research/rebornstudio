# features/ingest/lib

## 목적

영상 등록과 준비 과정의 표기 규칙을 둡니다. 판정은 서버가 하고, 이 폴더는 서버가 정한 값에 이름·색조·문장을 붙입니다. 화면 파일 밖에 두어서 렌더 없이 테스트합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `vocab.ts` | 준비 단계 사전. 단계 이름·색조(`STAGE_LABEL`·`STAGE_TONE`), 막힌 위치 이름(`BLOCKED_AT`·`blockedAtLabel`), 실행 단계·상태 이름(`RUN_STAGE_LABEL`·`RUN_STATUS_LABEL`), 묶음(`PREP_GROUPS`), 출처 이름, 분석 요청을 막은 사유 문구(`holdReasonText`), 색조별 유틸 표(`TONE_TEXT`·`TONE_FILL`·`TONE_DOT`·`TONE_CHIP_TONE`), 소요 시간 표기(`fmtElapsed`) |
| `deferred_note.ts` | 보류된 분석 요청의 행 안내(`deferredNote`). 서버가 준 짧은 사유와 다음 재요청 시각으로 문장을 만듭니다. 시각이 없거나 이미 지났으면 잠시 후라고 씁니다 |
| `dispatch_body.ts` | 분석 요청 본문(`dispatchBody`). 대상 영상(`video_ids`)만 싣습니다. 클립 길이는 모든 프로젝트가 같은 기본값을 쓰고, 서버는 모르는 필드를 422로 거절합니다 |
| `dispatch_toast.ts` | 분석 요청 결과 토스트(`dispatchToast`). 보류이면 서버가 다시 요청한다고 알리고, 실패이면 요청 대기 행을 가리킵니다. 게이트가 막은 영상은 서버가 준 사유별로 셉니다 |
| `row_action.ts` | 준비 타임라인 실패 줄의 조치(`failedRowAction`). 서버가 건너뛰기를 허용한 줄(`skippable`)은 건너뛰기, 그 밖의 줄은 재시도입니다. 장면 요약 줄의 재시도는 서버 값(`retryable`)을 따릅니다(`retryOffered`) |
| `retry_text.ts` | 분석 재요청과 요약 재요청의 문구와 결과 토스트(`retryToast`). 장면 요약을 받지 못한 영상(서버 `prep_blocked_stage`가 `summary`)은 확인 창이 `장면 요약만 다시 요청합니다`로 열리고 비용 금액을 싣지 않습니다. 영상 상세와 영상 등록 화면이 같이 씁니다 |
| `span.ts` | 분석 요청 대상의 총 길이(`fmtHourMin`, 1분 미만은 초)와 예상 비용(`costKrw`). 비용은 서버 비용 집계(`maiu.service._cost_core`)와 같은 산식(원 단위 반올림, 정확히 절반이면 짝수 쪽)을 쓰므로 통계의 비용과 같은 값이 나옵니다 |
| `meta_import.ts` | 메타 일괄 입력의 상한(`META_IMPORT_MAX_BYTES`·`META_IMPORT_MAX_ROWS`, 서버 테스트가 서버 값과 대조), 필드·판정 이름, 반영 요청 만들기(`metaApplyItems`, 변경 행만), 반영하지 않는 행 수 |
| `videoFiles.ts` | 등록 대상 영상 파일 판정(`VIDEO_EXTS`·`isVideoName`·`videoAccept`). 두 등록 경로가 함께 씁니다 |
| `storage_paths.ts` | 버킷 경로 직접 입력의 정리(`parseKeyLines`·`folderPrefix`)와 저장소에 없는 키 오류 읽기(`missingKeyOf`). 빈 줄·맨 앞 `/`·반복 줄을 정리하고, 영상이 아닌 줄은 따로 돌려주며, 폴더 경로 끝에 `/`를 붙입니다 |

`deferred_note`·`dispatch_body`·`dispatch_toast`·`meta_import`·`retry_text`·`row_action`·`span`·`storage_paths`는 같은 폴더에 `*.test.ts`가 있습니다. `dispatch_body.test.ts`는 본문의 필드가 `docs/api/openapi.json`의 `MaiuDispatchRequest`에 모두 있는지 대조합니다.

## 설계 이유

- 판정은 서버 한 곳(`_prep_stage_case`·`_ingest_bucket_case`)에 있습니다. 화면 이름이 바뀌어도 서버 값(`blocked` 등)은 그대로이고, 문구만 `vocab.ts`에서 바뀝니다.
- 화면은 프로젝트 설정을 보고 조치를 고르지 않습니다. 서버가 줄마다 허용한 조치(`skippable`)만 따릅니다.
- 같은 영상 묶음을 상태 타일과 확인 창 두 곳이 보여 줍니다. 곳마다 따로 반올림하면 같은 묶음이 서로 다른 수로 보이므로 계산을 `span.ts` 한 곳에 둡니다.
- 영상 파일은 MIME이 아니라 확장자로 판정합니다. 브라우저는 `.mxf`·`.ts` 파일에 빈 MIME을 주는 경우가 많아서, MIME으로 고르면 방송사 원본을 알림 없이 버리게 됩니다.
  확장자 목록은 서버(`services/media/video`의 `_VIDEO_EXTS`)와 같아야 합니다. 그래서 시트가 열릴 때 서버 값(`upload-limits`)을 받아 씁니다.

## 의존성

- `@reborn/contracts`, `shared/lib/fmtBytes`·`fmtSecs`·`time`, `shared/ui/Chip`(색조 타입), `lib/video_meta`(메타 필드 이름)
- 부르는 쪽: `../components/*`, `../index.ts`를 거쳐 `features/library`(단계 이름·색조)
