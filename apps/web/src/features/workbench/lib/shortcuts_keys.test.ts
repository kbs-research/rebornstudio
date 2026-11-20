/** 단축키 글자를 키캡 목록으로 나누는 `keysOf` 검사. */
import { describe, expect, it } from "vitest";

import { hit, keyOf, keysOf, PLAYBACK_SHORTCUTS } from "./shortcuts";

describe("keysOf", () => {
  it("수식키 조합을 캡 둘로 나누고 Enter를 기호로 쓴다", () => {
    expect(keysOf("⌘/Ctrl + Enter")).toEqual(["⌘", "↵"]);
    expect(keysOf("⌘/Ctrl + S")).toEqual(["⌘", "S"]);
  });
  it("짝 키를 나눈다", () => {
    expect(keysOf("J / L")).toEqual(["J", "L"]);
    expect(keysOf(", / .")).toEqual([",", "."]);
    expect(keysOf("[ / ]")).toEqual(["[", "]"]);
  });
  it("키 하나는 그대로 둔다", () => {
    expect(keysOf("Space")).toEqual(["Space"]);
    expect(keysOf("0")).toEqual(["0"]);
  });
});

describe("keyOf", () => {
  it("한글 입력 상태의 글자 키를 물리 키 자리로 읽는다", () => {
    expect(keyOf({ key: "ㅁ", code: "KeyA" })).toBe("a");
    expect(keyOf({ key: "ㅌ", code: "KeyX" })).toBe("x");
    expect(keyOf({ key: "Process", code: "KeyK" })).toBe("k");
  });
  it("영문 글자는 찍힌 글자를, 글자가 아닌 키는 그 이름을 쓴다", () => {
    expect(keyOf({ key: "A", code: "KeyA" })).toBe("a");
    expect(keyOf({ key: "q", code: "KeyA" })).toBe("q");
    expect(keyOf({ key: "Enter", code: "Enter" })).toBe("enter");
    expect(keyOf({ key: "[", code: "BracketLeft" })).toBe("[");
  });
  it("한글 입력 상태에서도 재생 키가 듣는다", () => {
    const e = { key: "ㅏ", code: "KeyK", metaKey: false, ctrlKey: false, altKey: false } as KeyboardEvent;
    expect(hit(PLAYBACK_SHORTCUTS, "pause", e)).toBe(true);
  });
});
