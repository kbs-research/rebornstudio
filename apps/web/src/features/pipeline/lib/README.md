# features/pipeline/lib

## 목적

작업 단계 화면의 순수 규칙을 둡니다. 단계별 대기 건수, 검수 화면의 초안·보유·입력 규칙, 품질검수 대표 지적, 완료 목록 필터 축이 여기 있습니다.
판정은 서버가 하고, 이 폴더는 받은 값을 나누거나 이름을 붙입니다. 화면 파일 밖에 두어서 렌더 없이 테스트합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `pipeline_counts.ts` | 단계별 대기 건수 `stageWaiting`(등록 칸은 지금 분석을 요청할 수 있는 영상 수), 스텝퍼 칸의 수 `stageCellCount`(완료 칸은 `completed_total`), 등록 칸의 보조 줄 `stageInProgress`(변환 중인 영상 수, 대기 수와 겹치지 않음) |
| `completed_filter.ts` | 완료 목록 필터 축 사전(`COMPLETED_FILTERS`, 값은 주소 `?filter=`)과 보이는 축(`visibleCompletedFilters`: 품질검수를 쓰지 않으면 규칙만 검사 축이 없음) |
| `lead_finding.ts` | 품질검수 보류의 대표 지적(`leadFinding`). 판정을 만든 가장 무거운 지적(오류, 경고, 참고 순)을 고르고, 무게가 같으면 단계 순서를 따릅니다 |
| `review_draft.ts` | 검수 초안의 복원·저장 형식(`readReviewDraft`·`reviewDraftBody`), 수정 후 승인 사유 문장(`modifyReason`), 화면을 떠날 때 마지막 초안을 작업 캐시에 넣는 `withReviewDraft` |
| `reject_reason.ts` | 검수 반려에 사유(칩이나 보충 설명)가 있는지 판정합니다(`rejectHasReason`). 서버 `services/reject_reasons.py`의 판정과 같은 조건입니다 |
| `stale_review.ts` | 판정하는 사이에 작업 상태가 바뀌었을 때(`not_submitted_{상태}`) 무엇으로 바뀌었는지 알리는 문구(`staleDecisionText`) |

파일마다 같은 폴더에 `*.test.ts`가 있습니다.

## 설계 이유

- 서버의 판정 규칙을 화면에서 다시 만들면 두 곳의 결과가 달라집니다. 반려 사유·보유 만료는 서버와 같은 조건을 씁니다. 단계 대기 수는 서버 값을 나누기만 합니다.
- 지적 문장만으로는 무엇이 틀렸는지 알 수 없습니다. 그래서 사유 없는 반려를 막습니다.
- 다른 건으로 옮겼다가 돌아온 검수 화면은 캐시로 초안을 복원합니다. 떠날 때 마지막 초안을 캐시에 넣지 않으면 이전 초안이 복원되고, 자동 저장이 서버 초안을 덮어씁니다.
- 품질검수 대표 지적은 판정을 만든 지적이어야 합니다. 앞 단계의 경고가 오류보다 먼저 보이면 무엇 때문에 보류됐는지 잘못 읽게 됩니다.

## 의존성

- `features/workbench`(문장 표시 타입), `shared/api/error_text`(`stale_review.ts`), `@reborn/contracts`
- 부르는 쪽: `../pages/*`, `../components/*`, `../index.ts`를 거쳐 `features/insights`(`stageCellCount`)·`features/workbench`(필터 축)
