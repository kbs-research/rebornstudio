/**
 * 작업 단계 기능의 공개 진입점. 다른 곳은 이 파일로만 가져온다.
 *
 * 개인 작업공간의 `내 작업`은 두 대기열의 다음 작업 카드(`NextWorkCard`)와 낱말(`QUEUE_SPEC`·`queueCountLabels`)을 같이 쓰고,
 * 다음 작업 뒤에 단계 배지(`pipelineKeys.summaryAll`)를 무효화한다. 개요 카드는 단계별 대기 건수와 품질검수 보류 수를 같은 키로 읽고,
 * 단계 칸에 표시하는 수는 스텝퍼와 같은 함수(`stageCellCount`)로 센다.
 * 영상 등록 단계는 머리 오른쪽 영역(`HeaderActions`)을,
 * 완료 워크벤치는 완료 클립 본문(`CompletedClipView`)과 거르기 축 사전을 가져간다.
 * 화면(`pages/`)과 라우트는 내놓지 않는다. 라우트 배열은 앱 라우터가 `routes.tsx`에서 직접 읽는다.
 */
export { fetchPipelineSummary, fetchQcHeldTotal, pipelineKeys } from "./api";
export { stageCellCount } from "./lib/pipeline_counts";
export {
  NextWorkCard,
  QUEUE_SPEC,
  nextErrorText,
  poolFailed,
  queueCountLabels,
  type NextErrorText,
  type QueueKind,
} from "./components/NextWorkCard";
export { HeaderActions } from "./components/HeaderActions";
export { CompletedClipView } from "./components/CompletedClipView";
export { COMPLETED_FILTERS, completedFilterOf, type CompletedFilter } from "./lib/completed_filter";
