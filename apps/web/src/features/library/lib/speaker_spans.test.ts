/** 화자별 발화 구간 모으기 검사. */
import { describe, expect, it } from "vitest";

import { speakerSpans } from "./speaker_spans";

describe("speakerSpans", () => {
  it("화자마다 처음 말한 시각과 마지막으로 끝낸 시각을 초로 모은다", () => {
    const out = speakerSpans(
      [{ pid: "1", name: "화자 1" }, { pid: "2", name: "화자 2" }],
      [
        { pid: "1", name: "화자 1", startMs: 48500, endMs: 60000, text: "a" },
        { pid: "2", name: "화자 2", startMs: 70200, endMs: 81000, text: "b" },
        { pid: "1", name: "화자 1", startMs: 61000, endMs: 70200, text: "c" },
      ],
    );
    expect(out).toEqual([
      { name: "화자 1", start: 48.5, end: 70.2 },
      { name: "화자 2", start: 70.2, end: 81 },
    ]);
  });
  it("발화가 없는 화자는 이름만 남는다", () => {
    expect(speakerSpans([{ pid: "3", name: "앵커" }], [])).toEqual([{ name: "앵커", start: null, end: null }]);
  });
});
