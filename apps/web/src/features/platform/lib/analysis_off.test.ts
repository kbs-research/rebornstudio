import { describe, expect, it } from "vitest";

import { ANALYSIS_OFF_HINT, analysisOffText, isAnalysisOff } from "./analysis_off";

describe("analysisOffText", () => {
  it("연동 진단의 환경 변수 괄호를 빼고 안내를 한 문구로 맞춘다", () => {
    const t = analysisOffText({ detail: "분석 연동이 꺼져 있습니다 (MAIU_ENABLED=false)", hint: "실제 분석을 쓰려면 분석 연동을 켜주세요." });
    expect(t.detail).toBe("분석 연동이 꺼져 있습니다");
    expect(t.hint).toBe(ANALYSIS_OFF_HINT);
  });

  it("분석 대상 점검과 연결 점검도 같은 안내를 쓴다", () => {
    const a = analysisOffText({ detail: "분석 연동이 꺼져 있습니다", hint: "실제 분석을 쓰려면 최고관리자가 서버에서 켜야 합니다." });
    const b = analysisOffText({ detail: "분석 연동이 꺼져 있어 시험용 분석 결과를 씁니다", hint: "실제 분석을 쓰려면 최고관리자가 서버에서 켜야 합니다." });
    expect(a.hint).toBe(ANALYSIS_OFF_HINT);
    expect(b.hint).toBe(ANALYSIS_OFF_HINT);
    expect(b.detail).toBe("분석 연동이 꺼져 있어 시험용 분석 결과를 씁니다");
  });

  it("다른 점검 결과는 그대로 둔다", () => {
    const c = { detail: "연결 2건 정상", hint: null };
    expect(isAnalysisOff(c)).toBe(false);
    expect(analysisOffText(c)).toBe(c);
  });
});
