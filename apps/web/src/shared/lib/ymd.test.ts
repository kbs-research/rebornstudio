import { describe, expect, it } from "vitest";

import { cleanYmdRange, isValidYmd } from "./ymd";

describe("isValidYmd", () => {
  it("있는 날짜만 받는다", () => {
    expect(isValidYmd("2026-09-30")).toBe(true);
    expect(isValidYmd("abc")).toBe(false);
    expect(isValidYmd("2026-13-45")).toBe(false);
    expect(isValidYmd("9999-99-99")).toBe(false);
    expect(isValidYmd("2026-02-30")).toBe(false);
    expect(isValidYmd("")).toBe(false);
  });
});

describe("cleanYmdRange", () => {
  it("틀린 날짜는 버린다", () => {
    expect(cleanYmdRange("abc", "2026-13-45")).toEqual({ from: "", to: "" });
    expect(cleanYmdRange("abc", "2026-09-01")).toEqual({ from: "", to: "2026-09-01" });
  });
  it("시작이 끝보다 늦으면 맞바꾼다", () => {
    expect(cleanYmdRange("2026-09-30", "2026-09-01")).toEqual({ from: "2026-09-01", to: "2026-09-30" });
  });
});
