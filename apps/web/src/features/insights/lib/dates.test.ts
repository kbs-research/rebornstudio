/**
 * 날짜·시각 표기를 검사한다.
 *
 * 표기는 앱 전체 규칙(`shared/lib/time`)과 같고, 올해가 아니면 연도를 붙인다.
 */
import { describe, expect, it } from "vitest";

import { fmtDay, fmtTime } from "./dates";

const NOW = new Date("2026-09-28T10:00:00+09:00");

describe("fmtDay", () => {
  it("올해 날짜는 월·일만 쓴다", () => {
    expect(fmtDay("2026-09-23T17:53:00+09:00", NOW)).toBe("9월 23일");
  });

  it("다른 해는 연도를 붙인다", () => {
    expect(fmtDay("2025-12-31T09:00:00+09:00", NOW)).toBe("2025년 12월 31일");
  });
});

describe("fmtTime", () => {
  it("시각만 24시간제로 쓴다", () => {
    expect(fmtTime("2026-09-23T09:02:00+09:00")).toBe("09:02");
  });
});
