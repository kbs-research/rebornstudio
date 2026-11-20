/** 목록 구간 표기 검사. 한 목록 안에서 형식이 달라지지 않는지 확인한다. */
import { describe, expect, it } from "vitest";

import { clipRangeFormatter } from "./clip_range";

describe("clipRangeFormatter", () => {
  it("1시간 미만 목록은 1분 전후 행이 같은 꼴이다", () => {
    const f = clipRangeFormatter(79);
    expect(f(0, 21.6)).toBe("0:00.0–0:21.6");
    expect(f(58.6, 79)).toBe("0:58.6–1:19.0");
  });

  it("반올림이 60초를 넘기면 분을 올린다", () => {
    expect(clipRangeFormatter(120)(59.96, 61)).toBe("1:00.0–1:01.0");
  });

  it("1시간 이상 목록은 시간을 앞에 붙인다", () => {
    const f = clipRangeFormatter(4898);
    expect(f(12, 75)).toBe("0:00:12–0:01:15");
    expect(f(3500, 4898)).toBe("0:58:20–1:21:38");
  });

  it("값을 모르면 대시다", () => {
    expect(clipRangeFormatter(10)(null, 5)).toBe("—");
  });
});
