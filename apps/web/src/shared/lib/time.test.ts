/** 날짜·시각 표기 테스트. 같은 해·다른 해, 7일 경계, 날짜 묶음 이름을 본다. */
import { describe, expect, it } from "vitest";

import { dayGroupLabel, fmtDate, fmtDateTime, fmtFullDateTime, fmtTime, whenLabel } from "./time";

const now = new Date(2026, 8, 28, 15, 0); // 로컬 2026년 9월 28일 15:00(월은 0부터 센다).
/** 기준 시각에서 `ms`만큼 전의 ISO 시각을 만든다. */
const ago = (ms: number) => new Date(now.getTime() - ms).toISOString();
const MIN = 60_000;

describe("whenLabel — 7일 안은 상대 시간, 그 뒤는 날짜", () => {
  it("분·시간·일", () => {
    expect(whenLabel(ago(10_000), now)).toBe("방금 전");
    expect(whenLabel(ago(5 * MIN), now)).toBe("5분 전");
    expect(whenLabel(ago(3 * 60 * MIN), now)).toBe("3시간 전");
    expect(whenLabel(ago(24 * 60 * MIN), now)).toBe("1일 전");
    expect(whenLabel(ago(6 * 24 * 60 * MIN), now)).toBe("6일 전");
  });
  it("7일이 넘으면 날짜로 쓴다 — 다른 해면 연도를 붙인다", () => {
    expect(whenLabel(ago(10 * 24 * 60 * MIN), now)).toBe("9월 18일");
    expect(whenLabel(new Date(2025, 11, 31, 9, 0).toISOString(), now)).toBe("2025년 12월 31일");
  });
});

describe("fmtDateTime — 날짜와 24시간제 시각", () => {
  it("같은 해면 연도를 빼고 분까지 쓴다", () => {
    expect(fmtDateTime(new Date(2026, 8, 10, 14, 37).toISOString(), now)).toBe("9월 10일 14:37");
    expect(fmtDateTime(new Date(2025, 0, 2, 7, 5).toISOString(), now)).toBe("2025년 1월 2일 07:05");
  });
});

describe("fmtDate — 날짜만, ISO와 YYYY-MM-DD를 같이 받는다", () => {
  it("같은 해면 연도를 빼고 다른 해면 붙인다", () => {
    expect(fmtDate("2026-09-01", now)).toBe("9월 1일");
    expect(fmtDate("2025-12-31", now)).toBe("2025년 12월 31일");
    expect(fmtDate(new Date(2026, 8, 23, 23, 59).toISOString(), now)).toBe("9월 23일");
  });
});

describe("fmtTime · fmtFullDateTime — 24시간제, 오전·오후를 쓰지 않는다", () => {
  it("시각만", () => {
    expect(fmtTime(new Date(2026, 8, 28, 9, 5).toISOString())).toBe("09:05");
    expect(fmtTime(new Date(2026, 8, 28, 18, 42).toISOString())).toBe("18:42");
  });
  it("툴팁용 전체 시각은 연도와 초까지 쓴다", () => {
    expect(fmtFullDateTime(new Date(2026, 8, 23, 18, 42, 5).toISOString())).toBe("2026년 9월 23일 18:42:05");
  });
});

describe("dayGroupLabel — 오늘 · 어제 · 날짜", () => {
  it("로컬 날짜로 묶는다", () => {
    expect(dayGroupLabel(new Date(2026, 8, 28, 0, 1).toISOString(), now)).toBe("오늘");
    expect(dayGroupLabel(new Date(2026, 8, 27, 23, 59).toISOString(), now)).toBe("어제");
    expect(dayGroupLabel(new Date(2026, 8, 24, 11, 20).toISOString(), now)).toBe("9월 24일");
    expect(dayGroupLabel(new Date(2025, 8, 28, 11, 20).toISOString(), now)).toBe("2025년 9월 28일");
  });
});
