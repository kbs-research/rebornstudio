import { describe, expect, it } from "vitest";

import { prepPoll, stageSignature } from "./prep_poll";

describe("prepPoll", () => {
  it("규격 검증이 진행 중이면 짧은 간격으로 다시 읽는다", () => {
    expect(prepPoll([
      { stage: "upload", status: "ok" },
      { stage: "probe", status: "running" },
      { stage: "transcode", status: "pending" },
    ])).toEqual({ timeline: 3_000, detail: 5_000 });
  });

  it("업로드가 대기면 짧은 간격으로 다시 읽는다", () => {
    expect(prepPoll([{ stage: "upload", status: "pending" }]).timeline).toBe(3_000);
  });

  it("뒤 단계만 남았으면 지금 간격으로 읽는다", () => {
    expect(prepPoll([
      { stage: "probe", status: "ok" },
      { stage: "transcode", status: "running" },
      { stage: "analyze", status: "pending" },
    ])).toEqual({ timeline: 10_000, detail: 15_000 });
  });

  it("다 끝났거나 실패·건너뜀만 남았으면 멈춘다", () => {
    expect(prepPoll([
      { stage: "probe", status: "ok" },
      { stage: "deident", status: "failed" },
      { stage: "publish", status: "skipped" },
    ])).toEqual({ timeline: false, detail: false });
    expect(prepPoll([])).toEqual({ timeline: false, detail: false });
  });
});

describe("stageSignature", () => {
  it("어느 단계의 상태가 바뀌면 값이 바뀌고, 같으면 같다", () => {
    const a = stageSignature([{ stage: "probe", status: "running" }, { stage: "transcode", status: "pending" }]);
    const b = stageSignature([{ stage: "probe", status: "ok" }, { stage: "transcode", status: "pending" }]);
    expect(a).not.toBe(b);
    expect(stageSignature([{ stage: "probe", status: "ok" }, { stage: "transcode", status: "pending" }])).toBe(b);
  });
});
