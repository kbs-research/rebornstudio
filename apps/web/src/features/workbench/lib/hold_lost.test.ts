/** 보유가 끝난 작업 판정(`hold_lost`) 검사. */
import { describe, expect, it } from "vitest";

import { isHoldLost, lostWhileOpen } from "./hold_lost";

describe("hold_lost", () => {
  it("대기열로 돌아간 작업에 늦게 저장한 실패는 보유가 끝난 것이다", () => {
    expect(isHoldLost("work_item_is_available")).toBe(true);
    expect(isHoldLost("not_your_claim")).toBe(true);
    expect(isHoldLost("caption_too_short")).toBe(false);
    expect(isHoldLost(undefined)).toBe(false);
  });

  it("내가 보유하던 작업이 403이면 보유가 끝난 것이고, 남의 작업이면 접근 권한 문제다", () => {
    expect(lostWhileOpen(403, "me", "me")).toBe(true);
    expect(lostWhileOpen(403, "other", "me")).toBe(false);
    expect(lostWhileOpen(404, "me", "me")).toBe(false);
    expect(lostWhileOpen(403, undefined, "me")).toBe(false);
  });
});
