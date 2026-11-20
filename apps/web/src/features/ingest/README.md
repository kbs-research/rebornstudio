# features/ingest

## 목적

영상 등록과 준비 과정(등록, 검증, 변환, 분석 등록, 분석)을 담당합니다. 작업 단계 화면의 영상 등록 단계 본문(`IngestPage`), 등록 시트와 두 등록 엔진(NCP 버킷·로컬 파일), 등록 Job 목록이 여기 있습니다.
준비 과정을 그리는 부품(상태 마크·영상 행·분포 막대·Job 머리와 분석 요청 카드·준비 타임라인), 준비 단계 문구 사전, 서버 호출과 쿼리 키도 이 폴더에 둡니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `api.ts` | 서버 호출과 키 팩토리 `ingestKeys`. 버킷 키·폴더 경로 등록, 업로드 조건, 영상 생성·멀티파트 업로드·확정·삭제, Job 이름·목록·상세, 상태별 수·영상 목록·조건 선택, 분석 잡·단가, 분석 요청·분석 재요청·검증 재검사·변환 재시도, 등록 묶음 메타 일괄 입력(양식 파일 받기·미리 보기·반영) |
| `index.ts` | 밖으로 내보내는 것: `IngestPage`(작업 단계 화면), 영상 상세의 준비 카드(`PrepTimeline`), 준비 단계 이름·톤 사전, `ingestKeys` |
| [`components/`](./components/README.md) | 등록 단계 본문, 등록 시트와 엔진, Job 목록, 준비 과정 부품 |
| [`lib/`](./lib/README.md) | 준비 단계 문구 사전, 보류 안내, 분석 요청 본문과 결과 문구, 실패 줄 조치, 길이·비용 계산, 영상 파일 판정, 버킷 경로 입력 정리 |

## 설계 이유

- 영상 등록 화면에는 자기 주소가 없습니다. 작업 단계 화면(`/p/:slug/pipeline?stage=ingest`)이 본문으로 `IngestPage`를 그립니다. 그래서 `IngestPage`는 `pages/`가 아니라 `components/`에 있고, 제목(`h1`)은 작업 단계 화면 머리가 그립니다.
- 등록은 늘 열어 두는 화면이 아니라 가끔 하는 작업(주 몇 회, 한 번에 수십 건)입니다. 그래서 페이지가 아니라 시트로 엽니다. 설정과 메타는 시트가 가지고, 두 등록 경로가 함께 씁니다.
  경로마다 설정을 따로 두면, 한 경로만 설정값을 하드코딩하는 결함이 경로가 늘 때마다 다시 생깁니다.
- 상태 판정은 서버 한 곳(`_prep_stage_case`·`_ingest_bucket_case`)이 합니다. 화면은 다시 계산하지 않고, `lib/vocab.ts`가 이름만 붙입니다.
- 쿼리 키는 `ingestKeys`로만 만듭니다. 영상 목록(`["videos", …]`)과 상태별 수는 콘텐츠 목록도 같은 키 앞부분으로 캐시합니다. 그래서 등록·조치 뒤에 두 화면이 함께 다시 받습니다.
  작업 단계 요약의 무효화는 `features/pipeline`의 `pipelineKeys`로 합니다.
- 분모를 밝힙니다. 상태 타일·단계 칩·분포 막대의 수는 서버가 전체를 집계한 값입니다. 화면에 실린 행 수와 다르면 그 사실을 표시합니다. 분석 요청 버튼의 건수와 대상도 서버가 셉니다.
- 준비 타임라인은 단계별 현재 상태와 실패·건너뜀 사유, 재시도·건너뛰기만 보여 줍니다. 시각·소요·시도 수는 쓰지 않습니다.
- 영상은 등록할 때 허가 상태로 들어오고 분석이 끝나면 클립이 바로 라벨링 대기로 갑니다. 그래서 이 화면에는 영상 허가·제외 조작이 없고, 상태 타일은 요청 대기·분석 중·문제 셋입니다. 서버 분류에도 확인 필요 상태가 없습니다.
- 비용은 등록 시점이 아니라 분석을 요청하는 시점에 생깁니다. 단가는 서버(`maiu/cost-stats`)에서 받습니다. 벤더 분석기가 없는 스택(`useAnalysisStack().usesVendorAnalyzer`)에서는 비용과 과금 문장을 그리지 않습니다.

## 의존성

- 부르는 것: `shared/ui/*`, `shared/api/*`, `shared/lib/*`(`clipboard`·`sha256`·`uuid` 포함), `shared/hooks/useOutsideClose`·`useDialogFocus`, `store/auth`·`store/project`, `hooks/useAnalysisStack`,
  `lib/video_meta`, `features/projects`(`useProjects`·`ConnectionNotice`), `features/pipeline`(`HeaderActions`·`pipelineKeys`)
- 부르는 쪽: `features/pipeline/pages/pipeline.tsx`(`IngestPage`), `features/library`(영상 상세의 준비 카드, 콘텐츠 목록과 영상 단계 칩의 단계 이름, 준비 폴링)

## 남은 일

- 준비 타임라인·영상 목록 키(`ingestKeys.prepTimeline`·`videosAll`)를 콘텐츠 기능(`libraryKeys`)이 같은 값으로 따로 만듭니다. 캐시는 함께 쓰지만, 한쪽 값만 고치면 두 키가 달라집니다. 한 팩토리로 모을지 정해야 합니다.
- `IngestPage`는 `features/pipeline`의 `HeaderActions`를 읽고, 작업 단계 화면은 `IngestPage`를 읽습니다. 그래서 두 기능이 서로의 `index.ts`를 읽습니다. `HeaderActions`는 도메인과 무관한 머리 슬롯이므로 `shared/`로 옮길지 정해야 합니다.
