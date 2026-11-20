/** 클립 한 줄 요약 규칙 검사. 영상 상세와 클립 보기가 같은 줄을 고르는지 확인한다. */
import { describe, expect, it } from "vitest";

import { clipSummary } from "./clip_summary";

describe("clipSummary", () => {
  it("장면 요약이 있으면 음성 인식보다 먼저 쓴다", () => {
    expect(clipSummary({ scene_summary: "앵커 스튜디오 멘트 장면", auto_caption_brief: "안녕하십니까" }))
      .toEqual({ text: "앵커 스튜디오 멘트 장면", source: "scene" });
  });

  it("장면 요약이 없으면 음성 인식 문장을 출처와 함께 돌려준다", () => {
    expect(clipSummary({ scene_summary: null, auto_caption_brief: "논란이 가열되고 있습니다" }))
      .toEqual({ text: "논란이 가열되고 있습니다", source: "speech" });
  });

  it("공백뿐인 값은 없는 것으로 본다", () => {
    expect(clipSummary({ scene_summary: "   ", auto_caption_brief: "문장" }))
      .toEqual({ text: "문장", source: "speech" });
    expect(clipSummary({ scene_summary: " ", auto_caption_brief: "" })).toBeNull();
  });

  it("둘 다 없으면 null이다", () => {
    expect(clipSummary({})).toBeNull();
  });
});
