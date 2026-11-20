/** 보류 목록의 대표 지적(`leadFinding`) 검사. */
import { describe, expect, it } from "vitest";

import { leadFinding } from "./lead_finding";

const warn = { check: "clip_length", severity: "warn", message: "클립이 기준보다 짧습니다" };
const error = { check: "deident", severity: "error", message: "비식별화 미흡으로 신고된 클립입니다" };
const info = { check: "labels", severity: "info", message: "세부 묘사가 비어 있습니다" };

describe("leadFinding", () => {
  it("판정을 만든 가장 무거운 지적을 고른다. 단계 순서가 앞선 경고보다 오류가 먼저다", () => {
    expect(leadFinding([info, warn, error])).toBe(error);
  });

  it("같은 무게면 단계 순서를 지킨다", () => {
    const warn2 = { ...warn, check: "pii", message: "다른 경고" };
    expect(leadFinding([warn, warn2])).toBe(warn);
  });

  it("참고 지적만 있으면 참고 지적을, 지적이 없으면 없다고 돌려준다", () => {
    expect(leadFinding([info])).toBe(info);
    expect(leadFinding([])).toBeUndefined();
    expect(leadFinding(undefined)).toBeUndefined();
  });
});
