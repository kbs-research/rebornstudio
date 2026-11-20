/**
 * 단계별 대기 건수. 파이프라인 스텝퍼와 사이드바가 같은 수를 보여 주게 하는 단일 출처다.
 * 스텝퍼와 사이드바가 각자 더하면 같은 단계를 두 수로 표시할 수 있다.
 *
 * `undefined`는 세지 않는 단계이거나 요약이 아직 오지 않은 경우이고, 0과 구분한다.
 * `완료`는 쌓인 산출물이라 배지로 재촉할 대기 건수가 없다.
 * 0으로 두면 "다 처리했다"로 읽히므로 `stageWaiting`은 완료를 세지 않고 `undefined`를 돌려준다.
 * 스텝퍼의 완료 칸에는 `stageCellCount`가 완료 수를 따로 적는다.
 */
import type { components } from "@reborn/contracts";

type PipelineSummary = components["schemas"]["PipelineSummary"];

/** 스텝퍼가 쓰는 단계 이름. 사이드바는 `stage-` 접두를 떼고 넘긴다. */
export type PipelineStage =
  | "ingest" | "label" | "review" | "done";

/**
 * 그 단계에서 사람의 처리를 기다리는 건수.
 * 세지 않는 단계이거나 요약이 아직 없으면 `undefined`다.
 */
export function stageWaiting(
  summary: PipelineSummary | undefined,
  stage: PipelineStage | string,
): number | undefined {
  if (!summary) return undefined;
  switch (stage) {
    // 등록 단계는 지금 분석을 요청할 수 있는 영상을 센다.
    case "ingest":
      return summary.awaiting_analysis;
    case "label":
      return summary.label_available;
    case "review":
      return summary.review_submitted;
    default:
      return undefined;   // `done` 포함. 모듈 docstring 참조
  }
}

/**
 * 스텝퍼 칸에 적는 수. 완료 칸만 대기 수가 아니라 쌓인 완료 수(`completed_total`)다.
 *
 * 완료 수는 완료 목록·내보내기와 같은 정의로 서버가 센다. 사이드바 배지는 이 함수가 아니라 `stageWaiting`을 읽어
 * 완료를 재촉하지 않는다. 요약이 아직 없으면 `undefined`다.
 */
export function stageCellCount(
  summary: PipelineSummary | undefined,
  stage: PipelineStage | string,
): number | undefined {
  if (stage === "done") return summary?.completed_total;
  return stageWaiting(summary, stage);
}

/**
 * 스텝퍼 칸의 보조 줄에 적는 진행 중인 수. 등록 칸에만 있고 준비 중인 영상 수(`preparing`)다.
 *
 * 대기 수와 겹치지 않는 서버 분류라 더하지 않고 따로 적는다. 이 줄이 없으면 등록 직후 변환이 도는 동안
 * 대기 수가 0일 때 칸에 `0 대기 영상`만 보여 등록한 영상이 사라진 것처럼 읽힌다.
 * 0이거나 요약이 없거나 다른 단계면 `undefined`이고, 그때는 이 줄을 그리지 않는다.
 */
export function stageInProgress(
  summary: PipelineSummary | undefined,
  stage: PipelineStage | string,
): number | undefined {
  if (stage !== "ingest") return undefined;
  const n = summary?.preparing;
  return n ? n : undefined;
}
