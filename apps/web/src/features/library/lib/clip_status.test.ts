/** 클립 상태 칩 선택 검사. 작업 상태와 클립 상태 중 무엇을 고르는지 확인한다. */
import { describe, expect, it } from "vitest";

import { clipStatusView } from "./clip_status";

describe("clipStatusView", () => {
  it("작업 상태가 있으면 작업 상태를 쓴다", () => {
    expect(clipStatusView({ work_status: "rejected", rights_status: "cleared" }))
      .toEqual({ text: "재작업 대기", tone: "bad" });
  });
  it("티켓이 없으면 클립 상태를 쓴다", () => {
    expect(clipStatusView({ work_status: null, rights_status: "legal_review" })?.text)
      .toBe("비식별화 신고");
  });
  it("정상 클립에 티켓이 생기기 전이면 칩이 없다", () => {
    expect(clipStatusView({ work_status: null, rights_status: "cleared" })).toBeNull();
  });
});
