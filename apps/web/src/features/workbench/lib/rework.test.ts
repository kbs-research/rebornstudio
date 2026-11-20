/** 재작업 판정(`isRework`) 검사. */
import { describe, expect, it } from "vitest";

import { isRework } from "./rework";

describe("isRework", () => {
  it("반려된 뒤 재작업으로 돌아온 건은 재작업이다", () => {
    expect(isRework({ latestVerdict: "reject", rejectionCount: 1 })).toBe(true);
    expect(isRework({ latestVerdict: "reject", rejectionCount: 3 })).toBe(true);
  });

  it("다른 사람이 받아 카운트가 0이면 재작업이 아니다 — 이력은 남아 있어도", () => {
    // 다른 사람이 큐에서 받으면 rejection_count가 0으로 돌아가지만 reviews 행은 남는다.
    // 이 검사는 다른 사람이 받은 반려를 이 사람의 반려처럼 표시하지 않는지 확인한다.
    expect(isRework({ latestVerdict: "reject", rejectionCount: 0 })).toBe(false);
  });

  it("승인·수정후승인은 카운트가 있어도 재작업이 아니다", () => {
    expect(isRework({ latestVerdict: "approve", rejectionCount: 2 })).toBe(false);
    expect(isRework({ latestVerdict: "modify_and_approve", rejectionCount: 2 })).toBe(false);
  });

  it("검수 이력이 없으면 재작업이 아니다", () => {
    expect(isRework({ latestVerdict: null, rejectionCount: 0 })).toBe(false);
    expect(isRework({})).toBe(false);
  });
});
