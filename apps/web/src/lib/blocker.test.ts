/** 납품 차단 사유 분리 테스트. 앞의 짧은 이름 조각만 필드로 나누고 문장 안의 콜론은 그대로 둔다. */
import { describe, expect, it } from "vitest";

import { splitBlocker } from "./blocker";

describe("splitBlocker", () => {
  it("스키마 검증 사유는 필드 키와 문장으로", () => {
    expect(splitBlocker("caption: 10자 이상이어야 합니다")).toEqual({
      field: "caption",
      reason: "10자 이상이어야 합니다",
    });
    expect(splitBlocker("rights_status: 미정")).toEqual({ field: "rights_status", reason: "미정" });
  });
  it("점이 든 경로도 필드다", () => {
    expect(splitBlocker("sentences.0.text: 비어 있습니다").field).toBe("sentences.0.text");
  });
  it("이름 조각이 없는 문장은 통째로 — 문장 안의 콜론을 이름으로 오인하지 않는다", () => {
    const s = "타임코드 오프셋 미측정 (원본 start_time 없음)";
    expect(splitBlocker(s)).toEqual({ field: null, reason: s });
    expect(splitBlocker("타임코드 오프셋이 원본과 어긋남: 3초").field).toBeNull();
  });
});
