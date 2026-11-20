/** 단계별 대기 건수 검사. 스텝퍼와 사이드바가 같은 수를 보이는지 확인한다. */
import { describe, expect, it } from "vitest";
import type { components } from "@reborn/contracts";

import { stageCellCount, stageInProgress, stageWaiting } from "./pipeline_counts";

type PipelineSummary = components["schemas"]["PipelineSummary"];

const summary = {
  awaiting_analysis: 3,
  label_available: 4,
  review_submitted: 5,
  completed_total: 9,
} as unknown as PipelineSummary;

describe("stageWaiting", () => {
  it("등록은 분석 대기", () => {
    expect(stageWaiting(summary, "ingest")).toBe(3);
  });
  it("라벨링·검수는 각자 한 칸", () => {
    expect(stageWaiting(summary, "label")).toBe(4);
    expect(stageWaiting(summary, "review")).toBe(5);
  });
  it("완료와 미도착은 0이 아니라 undefined", () => {
    expect(stageWaiting(summary, "done")).toBeUndefined();
    expect(stageWaiting(undefined, "label")).toBeUndefined();
  });
});

describe("stageCellCount", () => {
  it("완료 칸은 완료 수를 적고 대기 수는 여전히 세지 않는다", () => {
    expect(stageCellCount(summary, "done")).toBe(9);
    expect(stageWaiting(summary, "done")).toBeUndefined();
  });
  it("나머지 칸은 대기 수와 같다", () => {
    expect(stageCellCount(summary, "label")).toBe(4);
    expect(stageCellCount(undefined, "done")).toBeUndefined();
  });
});

describe("stageInProgress", () => {
  it("등록 칸은 변환 중인 영상 수를 따로 적고 대기 수에 더하지 않는다", () => {
    const s = { ...summary, preparing: 24 } as PipelineSummary;
    expect(stageInProgress(s, "ingest")).toBe(24);
    expect(stageWaiting(s, "ingest")).toBe(3);
  });
  it("0·미도착·다른 단계는 줄을 그리지 않는다", () => {
    expect(stageInProgress({ ...summary, preparing: 0 } as PipelineSummary, "ingest")).toBeUndefined();
    expect(stageInProgress(undefined, "ingest")).toBeUndefined();
    expect(stageInProgress({ ...summary, preparing: 3 } as PipelineSummary, "label")).toBeUndefined();
  });
});
