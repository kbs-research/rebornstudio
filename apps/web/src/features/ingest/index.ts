/**
 * 영상 등록·준비 기능의 공개 진입점. 다른 곳은 이 파일로만 등록 화면·준비 카드·준비 단계 사전을 가져온다.
 *
 * 작업 단계 화면은 영상 등록 단계의 본문(`IngestPage`)을, 영상 상세는 준비 타임라인 카드를 쓴다.
 * 콘텐츠 목록과 영상 단계 칩은 준비 단계 이름과 막힌 위치 이름을 쓴다. 단계 이름을 화면마다 따로 두면 한쪽만 고쳐질 수 있다.
 * 키 팩토리는 등록·조치 뒤 같은 영상 목록·상태별 수·준비 타임라인을 무효화하는 화면이 쓴다.
 * 라우트(`routes.tsx`)는 내놓지 않는다. 라우터가 `routes.tsx`를 직접 가져간다.
 */
export { ingestKeys, type RetryResult } from "./api";
export { IngestPage } from "./components/IngestPage";
export { PrepTimeline } from "./components/PrepTimeline";
export {
  isSummaryBlocked,
  retryFailTitle, retryToast,
  SUMMARY_CONFIRM,
  SUMMARY_RETRY_LABEL,
} from "./lib/retry_text";
export {
  blockedAtLabel,
  RUN_STAGE_LABEL,
  STAGE_LABEL,
  STAGE_TONE,
  TONE_CHIP_TONE,
  TONE_DOT,
  type PrepBlockedStage,
  type PrepStage,
  type PrepTimelineEntry,
  type Tone,
} from "./lib/vocab";
