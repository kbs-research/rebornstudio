/** 날짜 입력 테스트. 입력하는 대로 `YYYY-MM-DD`로 맞추는지, 날짜 검사·반영하지 못한 사유·연도 범위를 검증한다. */
import { describe, expect, it } from "vitest";

import { checkYmd, pendingProblem, typeYmd, yearRange } from "./DatePicker";

describe("typeYmd — 치는 대로 YYYY-MM-DD", () => {
  it("숫자만 쳐도 하이픈을 넣는다", () => {
    expect(typeYmd("1990")).toBe("1990");
    expect(typeYmd("19900")).toBe("1990-0");
    expect(typeYmd("199005")).toBe("1990-05");
    expect(typeYmd("19900521")).toBe("1990-05-21");
  });
  it("하이픈을 직접 쳐도 같고, 8자리를 넘는 숫자와 다른 글자는 버린다", () => {
    expect(typeYmd("1990-05-21")).toBe("1990-05-21");
    expect(typeYmd("1990.05.21")).toBe("1990-05-21");
    expect(typeYmd("199005219")).toBe("1990-05-21");
  });
});

describe("checkYmd — 쓸 수 있는 날짜인가", () => {
  it("다 치지 않았거나 없는 날짜면 이유를 말한다", () => {
    expect(checkYmd("1990-05")).toMatch(/모두 입력/);
    expect(checkYmd("2026-02-30")).toBe("없는 날짜입니다.");
    expect(checkYmd("2026-13-01")).toBe("없는 날짜입니다.");
  });
  it("범위 밖이면 경계를 말한다", () => {
    expect(checkYmd("2030-01-01", undefined, "2026-09-28")).toBe("2026-09-28 이전 날짜를 입력하세요.");
    expect(checkYmd("2020-01-01", "2026-01-01")).toBe("2026-01-01 이후 날짜를 입력하세요.");
  });
  it("쓸 수 있으면 null", () => {
    expect(checkYmd("1990-05-21", undefined, "2026-09-28")).toBeNull();
  });
});

describe("yearRange — 연도 선택지", () => {
  const now = new Date(2026, 8, 28);
  it("생년월일(max=오늘)이면 올해까지, 앞으로 100년을 연다", () => {
    expect(yearRange(2026, undefined, "2026-09-28", now)).toEqual([1926, 2026]);
  });
  it("보고 있는 해는 늘 들어간다", () => {
    expect(yearRange(1900, undefined, "2026-09-28", now)).toEqual([1900, 2026]);
  });
  it("범위가 없으면 뒤로 20년까지 연다 (계약 기한·공지 만료)", () => {
    expect(yearRange(2026, undefined, undefined, now)).toEqual([1926, 2046]);
  });
});

describe("pendingProblem — 친 날짜가 폼 값에 반영되지 못한 사유", () => {
  it("범위 밖·없는 날짜·덜 친 날짜는 사유가 있어 폼이 제출을 막는다", () => {
    expect(pendingProblem("2020-01-01", "", "2026-10-01")).toBe("2026-10-01 이후 날짜를 입력하세요.");
    expect(pendingProblem("2026-02-30", "1990-01-01")).toBe("없는 날짜입니다.");
    expect(pendingProblem("2026-1", "")).toMatch(/모두 입력/);
  });
  it("반영된 값이거나 비운 칸이면 사유가 없다", () => {
    expect(pendingProblem("2026-10-02", "2026-10-02", "2026-10-01")).toBeNull();
    expect(pendingProblem("", "2026-10-02")).toBeNull();
  });
  it("폼이 이미 가진 값은 범위 밖이어도 사유로 세지 않는다", () => {
    // 수정 창은 지난 만료일을 그대로 보여 준다. 다시 고르지 않으면 보내지 않으므로 사유 없음(`null`)이 맞다.
    expect(pendingProblem("2026-09-01", "2026-09-01", "2026-10-01")).toBeNull();
  });
});
