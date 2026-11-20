# features/pipeline

## 목적

작업 단계 기능을 담당합니다. 사람이 클립을 판정하고 다음 단계로 넘기는 흐름의 화면입니다. 작업 단계 화면(`/p/:slug/pipeline?stage=…`)과 단계 패널, 검수 워크벤치(`/review/:id`)가 여기 있습니다.
캡션 품질검수(`/p/:slug/qc`), 완료 클립 상세(`/p/:slug/completed/:workItemId`), 단계 규칙과 서버 호출도 이 폴더에 둡니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `api.ts` | 서버 호출과 키 팩토리 `pipelineKeys`. 단계 요약, 완료 목록·집계·상세, 내보내기, 반려 누적 목록, 검수(작업 한 건·판정 이력·라벨 이력·품질검수 결과·판정·초안·비식별화 신고), 비식별화 신고 목록과 처리(재개·제외), 품질검수(보류·해제·다시 검사·캡션 고치기) |
| `routes.tsx` | 라우트 배열 네 개. 품질검수(`qcRoutes`)·작업 단계(`pipelineRoutes`)·완료 상세(`completedDetailRoutes`)·검수(`reviewRoutes`). 전역 `/review`는 `app/redirects`의 `ToPipeline`으로 보냅니다 |
| `index.ts` | 밖으로 내보내는 것: 단계 요약·품질검수 보류 수 조회와 `pipelineKeys`, `stageCellCount`, 다음 작업 카드(`NextWorkCard`)와 대기열 낱말(`QUEUE_SPEC`·`queueCountLabels`), 실패 판정(`nextErrorText`·`poolFailed`), `HeaderActions`, `CompletedClipView`, 완료 목록 필터 축(`COMPLETED_FILTERS`·`completedFilterOf`). 화면과 라우트는 내보내지 않습니다 |
| [`pages/`](./pages/README.md) | 작업 단계·검수·품질검수·완료 상세 화면 |
| [`components/`](./components/README.md) | 단계 패널, 대기열 카드, 반려 누적 카드, 비식별화 신고 카드, 내보내기, 완료 클립 본문 |
| [`lib/`](./lib/README.md) | 단계 대기 건수, 검수 초안과 입력 규칙, 품질검수 대표 지적, 완료 목록 필터 축 등 |

## 설계 이유

- 두 대기열(라벨링·검수)의 보유 목록·대기 수·다음 작업은 `features/me`의 훅(`useLabelQueue`·`useReviewQueue`)과 `meKeys`를 씁니다. 내 작업 화면과 같은 요청, 같은 키이므로 한쪽에서 다음 작업을 받으면 다른 쪽의 수도 함께 바뀝니다.
- 작업 한 건의 키(`workItem`·`workLabels`·`workReviews`)는 라벨링 워크벤치가 같은 배열로 읽습니다. 검수 판정 뒤 `workAll()`(`["work"]`)을 무효화하면 그 캐시와 라벨링 보유 목록까지 다시 받습니다.
- 클립·영상 한 건은 콘텐츠 기능(`features/library`)의 조회와 키를 씁니다. 조회 함수를 두 개 두면 요청과 캐시가 둘로 나뉩니다.
- 서버의 판정 규칙을 화면에서 다시 만들지 않습니다. 단계 대기 건수는 서버 요약을 나눠 읽기만 합니다(`stageWaiting`). 비식별화 신고 카드의 수는 서버 목록의 `total`을 쓰고, 실린 행이 그보다 적으면 `전체 N건 중 M건`으로 밝힙니다.
  완료 목록의 필터는 서버가 적용합니다(`?filter=`). 화면이 불러온 행만 거르면 납품 불가 없음 같은 문장이 잘못 표시됩니다.
- 요청 주소는 동작마다 전체를 적습니다. 주소를 조립하면 화면이 부르는 서버 라우트를 세는 검사(`scripts/ci/check-orphan-routes.mjs`)가 주소를 읽지 못합니다.
- 단계는 쿼리 파라미터(`?stage=`)로 나타냅니다. 그래서 사이드바의 활성 항목 판정과 브레드크럼이 `location.search`를 봅니다. 경로만 보면 모든 단계가 같은 화면으로 판정됩니다.
- 사이드바의 작업 단계 항목에는 배지를 달지 않습니다. 영상 단위(등록)와 작업 단위(라벨링·검수)를 합쳐 세면 단위가 다른 수가 섞입니다. 단계별 수는 스텝퍼가 `stageWaiting`으로 보입니다.
- 단계를 더할 때는 그 단계를 볼 역할도 함께 정합니다. 역할별 단계 표는 `src/lib/roles.ts`의 `STAGE_ROLES`·`visibleStages`에 있습니다.

## 의존성

- 부르는 것: `shared/ui/*`, `shared/api/*`, `shared/lib/*`(`diff`·`josa`·`download` 포함), `shared/hooks/useUnsavedGuard`, `shell`(`RoleGuard`·`AccessDenied`·`nav-config`), `app/redirects`, `store/auth`·`store/project`,
  `components/StatusBadge`·`ReadOnlyLabel`, `hooks/useSlugProject`, `lib/roles`·`vocab`·`anon`·`blocker`·`video_meta`,
  `features/me`, `features/projects`, `features/ingest`(`IngestPage`), `features/workbench`(워크벤치 셸·단축키·캡션 규칙), `features/library`(플레이어·클립과 영상 조회)
- 부르는 쪽: `app/router.tsx`, `features/me`(대기열 낱말·`pipelineKeys`), `features/insights`(단계 요약·보류 수), `features/ingest`(`HeaderActions`·`pipelineKeys`), `features/workbench`(`CompletedClipView`·필터 축)

## 남은 일

- 워크벤치 기능(`workbenchKeys.item`·`labels`·`reviews`·`completedSummary`)이 작업 한 건·라벨·검수 키와 완료 요약 키를 같은 값으로 따로 만듭니다. 한쪽 값만 고치면 두 키가 달라집니다. 한 팩토리로 모을지 정해야 합니다.
