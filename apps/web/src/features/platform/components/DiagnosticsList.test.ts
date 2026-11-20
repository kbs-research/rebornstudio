/** 연동 점검 요약이 확인 전 항목을 주의로 세지 않는지 확인한다. */
import { describe, expect, it } from "vitest";

import type { DiagnosticsReport } from "@/shared/api/client";

import { diagnosticsSummary } from "./DiagnosticsList";

/** 주어진 상태들로 테스트용 진단 결과를 만든다. */
const report = (...statuses: string[]): DiagnosticsReport => ({
  checks: statuses.map((status, i) => ({ key: `k${i}`, label: `항목 ${i}`, status, detail: "" })),
  failed: statuses.filter((s) => s === "fail").length,
});

describe("diagnosticsSummary", () => {
  it("확인 전 항목은 주의가 아니다", () => {
    expect(diagnosticsSummary(report("ok", "ok", "unchecked"))).toBe("3건 중 2건 정상 · 확인 전 1");
  });

  it("모두 정상이면 그렇게 말한다", () => {
    expect(diagnosticsSummary(report("ok", "ok"))).toBe("2건 모두 정상");
  });

  it("분모와 정상 수를 먼저 말하고 실패·주의·확인 전 순으로 붙인다", () => {
    expect(diagnosticsSummary(report("fail", "warn", "unchecked", "ok"))).toBe("4건 중 1건 정상 · 실패 1 · 주의 1 · 확인 전 1");
    expect(diagnosticsSummary(report("warn", "ok"))).toBe("2건 중 1건 정상 · 주의 1");
  });
});
