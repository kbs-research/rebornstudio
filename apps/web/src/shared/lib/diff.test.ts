/** 교정 diff 테스트. 어절로 맞추고, 바뀐 어절 안은 글자 단위로 좁힌다(한국어는 어절이 길다). */
import { describe, expect, it } from "vitest";

import { countChanges, wordDiff } from "./diff";

describe("wordDiff", () => {
  it("어미 한 조각만 바뀌면 그 글자만 짚는다", () => {
    expect(wordDiff("소환했습니다.", "소환했다.")).toEqual([
      { type: "eq", text: "소환했", tight: true },
      { type: "del", text: "습니", tight: true },
      { type: "eq", text: "다." },
    ]);
  });
  it("남남인 어절은 통째 교체 — 글자로 쪼개면 못 읽는다", () => {
    const ops = wordDiff("사과", "자동차");
    expect(ops).toEqual([
      { type: "del", text: "사과" },
      { type: "add", text: "자동차" },
    ]);
  });
});

describe("countChanges", () => {
  it("줄바꿈·연속 공백만 다르면 고친 곳 0", () => {
    expect(countChanges(wordDiff("a  b\nc", "a b c"))).toBe(0);
  });
  it("이어진 del·add 한 덩어리는 한 곳", () => {
    expect(countChanges(wordDiff("사과", "자동차"))).toBe(1);
    expect(countChanges(wordDiff("가 나", "가 다 나"))).toBe(1);
  });
});
