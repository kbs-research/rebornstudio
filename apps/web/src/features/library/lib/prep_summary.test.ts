import { describe, expect, it } from "vitest";

import { REVIEW_FALLBACK, summaryMark, summaryNotes } from "./prep_summary";

const ok = (stage: "upload" | "probe" | "publish") => ({ stage, status: "ok" as const });

describe("summaryMark", () => {
  it("확인 필요가 있으면 완료여도 확인 필요로 표시한다", () => {
    expect(summaryMark({ stage: "deident", status: "ok", review_needed: true })).toBe("review");
  });

  it("건너뛴 단계는 건너뜀, 나머지는 완료로 표시한다", () => {
    expect(summaryMark({ stage: "transcode", status: "skipped" })).toBe("skipped");
    expect(summaryMark(ok("upload"))).toBe("ok");
  });
});

describe("summaryNotes", () => {
  it("확인 필요를 건너뜀 사유보다 먼저 둔다", () => {
    const notes = summaryNotes([
      ok("upload"),
      { stage: "transcode", status: "skipped", skipped_reason: "원본이 이미 분석 규격입니다." },
      { stage: "deident", status: "ok", review_needed: true, review_note: "얼굴을 찾지 못했습니다." },
      { stage: "scene_split", status: "skipped", skipped_reason: "분석 단계에서 장면을 함께 나눕니다." },
    ]);
    expect(notes).toEqual([
      { stage: "deident", tone: "warn", text: "얼굴을 찾지 못했습니다." },
      { stage: "transcode", tone: "mute", text: "원본이 이미 분석 규격입니다." },
      { stage: "scene_split", tone: "mute", text: "분석 단계에서 장면을 함께 나눕니다." },
    ]);
  });

  it("사유가 없는 건너뜀은 줄을 만들지 않는다", () => {
    expect(summaryNotes([{ stage: "transcode", status: "skipped", skipped_reason: null }])).toEqual([]);
  });

  it("확인 필요 사유가 비면 대체 문장을 쓴다", () => {
    expect(summaryNotes([{ stage: "deident", status: "ok", review_needed: true, review_note: null }]))
      .toEqual([{ stage: "deident", tone: "warn", text: REVIEW_FALLBACK }]);
  });

  it("모두 완료면 줄이 없다", () => {
    expect(summaryNotes([ok("upload"), ok("probe"), ok("publish")])).toEqual([]);
  });
});
