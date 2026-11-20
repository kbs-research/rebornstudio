/** 검수 반려 사유 유무 판정 검사. 서버 `validate_decision`과 같은 조건이다. */
import { describe, expect, it } from "vitest";

import { rejectHasReason } from "./reject_reason";

describe("rejectHasReason", () => {
  it("사유 칩도 보충 설명도 없으면 사유가 없다 — 지적 문장만으로는 무엇이 틀렸는지 모른다", () => {
    expect(rejectHasReason([], "")).toBe(false);
    expect(rejectHasReason([], "   ")).toBe(false);
  });

  it("사유 칩 하나나 보충 설명 한 줄이면 된다", () => {
    expect(rejectHasReason(["caption_inaccurate"], "")).toBe(true);
    expect(rejectHasReason([], "고유명사 표기 확인")).toBe(true);
  });
});
