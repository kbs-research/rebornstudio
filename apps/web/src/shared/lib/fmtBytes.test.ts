/** 파일 규격 표기 테스트. 비트레이트는 1000 단위로 나누고 1 Mbps부터 Mbps로 쓴다. */
import { describe, expect, it } from "vitest";

import { fmtBitRate } from "./fmtBytes";

describe("fmtBitRate", () => {
  it("1 Mbps 이상은 소수 한 자리 Mbps로 쓴다", () => {
    expect(fmtBitRate(8_200_000)).toBe("8.2 Mbps");
    expect(fmtBitRate(1_000_000)).toBe("1.0 Mbps");
  });
  it("1 Mbps 미만은 kbps로 쓴다", () => {
    expect(fmtBitRate(298_483)).toBe("298 kbps");
  });
  it("반올림해 1000 kbps가 되는 값은 Mbps로 쓴다", () => {
    expect(fmtBitRate(999_600)).toBe("1.0 Mbps");
  });
});
