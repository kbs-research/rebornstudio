import { describe, expect, it } from "vitest";

import { decisionCountLabel, decisionTarget, decisionView } from "./decisions";

describe("decisionView", () => {
  it("검수 판정을 판정 사전의 이름으로 부른다", () => {
    expect(decisionView({ decision: "reject" })).toEqual({ label: "반려", tone: "bad" });
    expect(decisionView({ decision: "modify_and_approve" })).toEqual({ label: "수정 후 승인", tone: "note" });
  });

  it("모르는 값은 원문과 무채색으로 남긴다", () => {
    expect(decisionView({ decision: "new_value" })).toEqual({ label: "new_value", tone: "neutral" });
  });
});

describe("decisionTarget", () => {
  it("클립 판정은 제목과 구간, 영상 판정은 영상 전체라고 쓴다", () => {
    expect(decisionTarget({ video_title: "9시 뉴스 샘플", clip_id: "c", clip_start_seconds: 42, clip_end_seconds: 78 })).toBe("9시 뉴스 샘플 · 0:42–1:18");
    expect(decisionTarget({ video_title: "9시 뉴스 샘플", clip_id: null })).toBe("9시 뉴스 샘플 · 영상 전체");
    expect(decisionTarget({ video_title: null, clip_id: "c" })).toBe("제목 없음");
  });
});

describe("decisionCountLabel", () => {
  it("덜 받았으면 분모와 받은 수를 함께 쓴다", () => {
    expect(decisionCountLabel(20, 57)).toBe("전체 57건 중 20건");
    expect(decisionCountLabel(57, 57)).toBe("전체 57건");
    expect(decisionCountLabel(0, 0)).toBe("전체 0건");
  });
});
