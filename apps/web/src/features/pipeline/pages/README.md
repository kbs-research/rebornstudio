# features/pipeline/pages

## 목적

작업 단계 기능의 라우트 화면을 둡니다. 주소 연결은 `../routes.tsx`가 합니다.

## 주요 파일

| 파일 | 경로 | 역할 |
|---|---|---|
| `pipeline.tsx` | `/p/:slug/pipeline?stage=…` | 작업 단계 화면. 단계는 영상 등록·라벨링·검수·완료입니다. 보이는 단계와 처음 여는 단계는 `lib/roles`의 단계 표가 정하고, 스텝퍼의 수는 `../lib/pipeline_counts.ts`가 셉니다. 활성 단계의 패널을 그리고, 영상 등록 단계에서는 `features/ingest`의 `IngestPage`를, 라벨링 단계에서는 작업 패널 아래에 비식별화 신고 카드와 반려 누적 카드를 그립니다 |
| `review.tsx` | `/review/:id` | 검수 워크벤치. 판정은 문장별 표시(확인·수정·삭제·지적)에서 정해지고, 판정 전의 표시는 검수 초안으로 서버에 저장합니다. 작업 id를 key로 걸어서 건이 바뀌면 새로 마운트합니다. 판정 요청에는 보여 준 제출본의 라벨 ID(`base_label_id`)를 넣습니다. 수정 후 승인의 수정본이 프로젝트 캡션 기준(`caption_min_tokens`)에 못 미치면 서버와 같은 기준으로 제출 버튼을 잠급니다. 판정·신고 뒤에는 내 작업으로 돌아갑니다. 다음 검수는 그 화면의 버튼 하나가 엽니다. 반납과 보유 시간은 없습니다 |
| `qc.tsx` | `/p/:slug/qc` | 캡션 품질검수. 관리자가 처리할 보류 목록과, 행을 누르면 열리는 오른쪽 드로어(지적·캡션·검사한 단계)를 둡니다. 처리는 캡션 고치기·다시 검사·보류 해제 셋입니다. 마지막 다시 검사가 AI 검사 실패로 끝난 보류 행에는 실패 시각과 이전 판정 표시를 먼저 씁니다 |
| `CompletedDetailPage.tsx` | `/p/:slug/completed/:workItemId` | 완료 클립 상세. 좁은 폭과 북마크용 화면이고, 본문은 `../components/CompletedClipView.tsx` 하나입니다 |

## 설계 이유

- 검수 화면에서 건이 바뀔 때 새로 마운트하지 않으면 앞 건의 표시와 수정본이 다음 건으로 따라갑니다.
- 판정 요청에 보여 준 제출본의 라벨 ID를 넣습니다. 그사이 라벨러가 다시 제출했다면 서버가 판정을 거절하므로 다른 제출본을 잘못 승인하지 않습니다.
- 품질검수 화면은 지금 고칠 보류 작업만 보입니다. 작업 한 건의 검사 기록은 검수 화면이 `GET /work/{id}/qc`로 보입니다.

## 의존성

- `../api.ts`, `../components/*`, `../lib/*`, `shared/ui/*`, `shared/hooks/useUnsavedGuard`, `hooks/useSlugProject`, `components/ReadOnlyLabel`, `store/auth`·`store/project`,
  `features/workbench`(워크벤치 셸·단축키), `features/library`(플레이어·클립 조회), `features/me`(보유 목록·보유 무효화), `features/projects`(`useProject`), `features/ingest`(`IngestPage`)
