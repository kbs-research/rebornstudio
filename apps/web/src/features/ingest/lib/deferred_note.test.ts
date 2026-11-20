import { describe, expect, it } from "vitest";

import { deferredNote } from "./deferred_note";

const now = new Date("2026-10-02T01:00:00Z");
const base = {
  kind: "throttled",
  label: "분석 서비스 요청량 제한",
  reason: "분석 서비스 요청량 제한을 초과했습니다: Throttle Limited (코드 410)",
  at: "2026-10-02T00:59:30Z",
  retry_by: "2026-10-02T01:00:55Z",
};

describe("deferredNote", () => {
  it("짧은 사유와 다음 재요청까지의 분을 쓰고 전체 사유는 title로 둔다", () => {
    expect(deferredNote(base, now)).toEqual({
      text: "보류됨 · 분석 서비스 요청량 제한 · 1분 안에 자동으로 다시 요청합니다",
      title: base.reason,
    });
  });

  it("남은 시간은 분 단위로 올려 쓴다", () => {
    expect(deferredNote({ ...base, retry_by: "2026-10-02T01:01:25Z" }, now)?.text)
      .toContain("2분 안에");
  });

  it("시각이 없거나 지났으면 지어내지 않고 잠시 후로 쓴다", () => {
    expect(deferredNote({ ...base, retry_by: null }, now)?.text).toContain("잠시 후 자동으로");
    expect(deferredNote({ ...base, retry_by: "2026-10-02T00:59:00Z" }, now)?.text).toContain("잠시 후 자동으로");
  });

  it("보류가 아니면 안내가 없다", () => {
    expect(deferredNote(null, now)).toBeUndefined();
  });
});
