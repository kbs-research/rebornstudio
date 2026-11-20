import { describe, expect, it } from "vitest";

import { DEFERRED_HINT, FAILED_HINT, dispatchToast } from "./dispatch_toast";

const base = { created: [] as unknown[], deferred: 0, failed: 0, skipped: 0, held: 0 };

describe("dispatchToast", () => {
  it("보류는 서버가 다시 요청한다고 말하고 다시 누르라고 하지 않는다", () => {
    const t = dispatchToast({ ...base, deferred: 1 });
    expect(t).toEqual({ tone: "warning", title: "1개 보류", detail: DEFERRED_HINT });
    expect(t.detail).not.toContain("다시 요청해주세요");
  });

  it("실패는 문제 목록이 아니라 요청 대기 행을 가리킨다", () => {
    const t = dispatchToast({ ...base, failed: 1 });
    expect(t.detail).toBe(FAILED_HINT);
    expect(t.detail).not.toContain("문제 목록");
  });

  it("게이트가 막은 영상은 서버가 준 사유로 센다", () => {
    const t = dispatchToast({ ...base, held: 2, held_reasons: { not_probed: 1, preparing: 1 } });
    expect(t).toEqual({ tone: "warning", title: "2개 요청하지 않음", detail: "사유 · 규격 검증 전 1개 · 준비 중 1개" });
  });

  it("나간 것과 막힌 것을 한 줄에 함께 밝힌다", () => {
    const t = dispatchToast({ ...base, created: [{}], held: 1, held_reasons: { unmasked: 1 } });
    expect(t.title).toBe("영상 1개 분석 요청 · 1개 요청하지 않음");
    expect(t.detail).toBe("사유 · 비식별화 전 1개");
  });

  it("모두 나가면 성공이다", () => {
    expect(dispatchToast({ ...base, created: [{}, {}] }).tone).toBe("success");
  });
});
