/** `내 작업` 목록 캡션 미리보기 선택 검사. */
import { describe, expect, it } from "vitest";

import { queuePreview } from "./queue_preview";

const clip = { scene_summary: "보랏빛 조명이 감도는 무대 위에서", auto_caption_brief: "와 드디어 왔네요" };
const caption = (text: string) => ({ caption: { brief_sentences: [{ id: "a", text }] } });

describe("queuePreview", () => {
  it("검수 목록은 분석 원문이 아니라 제출된 캡션을 보인다", () => {
    expect(queuePreview({ payload: caption("소토가 3타점을 올렸다."), draft: null }, clip))
      .toEqual({ text: "소토가 3타점을 올렸다.", source: "caption" });
  });

  it("라벨러 목록은 임시저장이 있으면 그것을 직전 제출보다 먼저 보인다", () => {
    expect(queuePreview({ draft: caption("고친 캡션."), payload: caption("반려된 캡션.") }, clip)?.text)
      .toBe("고친 캡션.");
  });

  it("라벨러 캡션이 없으면 장면 요약, 없으면 음성 인식으로 대신하고 출처를 밝힌다", () => {
    expect(queuePreview({ payload: {}, draft: null }, clip))
      .toEqual({ text: "보랏빛 조명이 감도는 무대 위에서", source: "scene" });
    expect(queuePreview({ payload: {} }, { auto_caption_brief: "와 드디어 왔네요" }))
      .toEqual({ text: "와 드디어 왔네요", source: "speech" });
  });

  it("아무것도 없으면 null이다", () => {
    expect(queuePreview(undefined, undefined)).toBeNull();
    expect(queuePreview({ payload: { caption: { brief: "  " } } }, {})).toBeNull();
  });
});
