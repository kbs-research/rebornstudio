/** 분석 요청 대상의 총 길이·예상 비용 표기를 검사한다. */
import { describe, expect, it } from "vitest";

import { costKrw, fmtHourMin } from "./span";

describe("fmtHourMin", () => {
  it("1분 미만은 초로 쓴다", () => {
    expect(fmtHourMin(25)).toBe("25초");
    expect(fmtHourMin(59.4)).toBe("59초");
  });

  it("1분 이상은 분, 1시간 이상은 시간과 분으로 쓴다", () => {
    expect(fmtHourMin(60)).toBe("1분");
    expect(fmtHourMin(38 * 60 + 10)).toBe("38분");
    expect(fmtHourMin(5 * 3600 + 12 * 60)).toBe("5시간 12분");
  });

  it("반올림해 60초가 되는 값은 1분으로 쓴다", () => {
    expect(fmtHourMin(59.6)).toBe("1분");
  });
});

describe("costKrw", () => {
  /* 서버 `maiu.service._cost_core`의 산식은 round(길이 초 / 3600 × 시간당 단가)다. */
  it("서버 산식과 같은 값을 낸다", () => {
    expect(costKrw(25, 10_000)).toBe(69);
    expect(costKrw(3600, 10_000)).toBe(10_000);
    expect(costKrw(5 * 3600 + 12 * 60, 10_000)).toBe(52_000);
  });

  it("딱 절반은 서버(파이썬 round)처럼 짝수 쪽으로 반올림한다", () => {
    expect(costKrw(9, 200)).toBe(0);
    expect(costKrw(27, 200)).toBe(2);
  });

  it("단가나 길이가 없으면 null이다", () => {
    expect(costKrw(25, undefined)).toBeNull();
    expect(costKrw(0, 10_000)).toBeNull();
  });
});
