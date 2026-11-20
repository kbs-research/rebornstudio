/**
 * 준비 타임라인의 장면 요약 줄 렌더 테스트. 재요청 버튼의 유무와 이름이 서버 값을 따르는지 본다.
 * JSX 없이 `createElement`와 서버 렌더러로 그려 문자열로 확인한다.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PrepTimelineEntry } from "../lib/vocab";
import { PrepTimeline } from "./PrepTimeline";

const failedSummary = (retryable: boolean): PrepTimelineEntry => ({
  stage: "summarize",
  status: "failed",
  error: "장면 요약을 만들지 못했습니다",
  retryable,
} as PrepTimelineEntry);

function render(entry: PrepTimelineEntry, retryLabels?: Partial<Record<PrepTimelineEntry["stage"], string>>) {
  return renderToStaticMarkup(createElement(PrepTimeline, {
    entries: [entry], onRetry: () => {}, openFailed: true, retryLabels,
  }));
}

describe("PrepTimeline 장면 요약 줄", () => {
  it("서버가 연 요약 실패 줄은 요약 재요청 버튼과 사유를 그린다", () => {
    const html = render(failedSummary(true), { summarize: "요약 재요청" });
    expect(html).toContain("요약 재요청");
    expect(html).toContain("장면 요약을 만들지 못했습니다");
    expect(html).not.toContain("분석 재요청");
  });

  it("이름을 바꾸지 않으면 분석 재요청 이름을 쓴다", () => {
    expect(render(failedSummary(true))).toContain("분석 재요청");
  });

  it("서버가 열지 않은 요약 실패 줄에는 재요청 버튼을 두지 않는다", () => {
    const html = render(failedSummary(false), { summarize: "요약 재요청" });
    expect(html).not.toContain("요약 재요청");
    expect(html).not.toContain("분석 재요청");
    expect(html).toContain("장면 요약을 만들지 못했습니다");
  });
});
