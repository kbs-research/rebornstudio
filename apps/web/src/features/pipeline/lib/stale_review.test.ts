/** 판정 중 바뀐 상태 문구(`staleDecisionText`) 검사. */
import { describe, expect, it } from "vitest";

import { staleDecisionText } from "./stale_review";

describe("staleDecisionText", () => {
  it("다른 탭에서 승인한 작업은 이미 승인됐다고 말한다", () => {
    expect(staleDecisionText("not_submitted_approved")?.message).toBe("이미 승인된 작업입니다");
    expect(staleDecisionText("not_submitted_rejected")?.message).toBe("이미 반려된 작업입니다");
  });

  it("모르는 상태도 상태 변경으로 알리고, 상태 변경이 아니면 null이다", () => {
    expect(staleDecisionText("not_submitted_weird")?.message).toBe("판정할 수 없는 상태입니다");
    expect(staleDecisionText("not_your_review_claim")).toBeNull();
    expect(staleDecisionText(undefined)).toBeNull();
  });
});
