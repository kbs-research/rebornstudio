import { describe, expect, it } from "vitest";

import { folderPrefix, missingKeyOf, parseKeyLines } from "./storage_paths";

describe("parseKeyLines", () => {
  it("빈 줄과 앞뒤 공백, 맨 앞 슬래시를 정리하고 반복은 한 번만 남긴다", () => {
    const r = parseKeyLines("  news/a.mp4 \n\n/news/b.MOV\nnews/a.mp4\r\n");
    expect(r.keys).toEqual(["news/a.mp4", "news/b.MOV"]);
    expect(r.skipped).toEqual([]);
  });

  it("영상 확장자가 아닌 줄은 보내지 않고 따로 돌려준다", () => {
    const r = parseKeyLines("news/a.mp4\nnews/notes.json\nnews/folder/");
    expect(r.keys).toEqual(["news/a.mp4"]);
    expect(r.skipped).toEqual(["news/notes.json", "news/folder/"]);
  });

  it("서버가 준 확장자 목록이 있으면 그 목록으로 판정한다", () => {
    const r = parseKeyLines("a.mp4\nb.mxf", new Set(["mp4"]));
    expect(r.keys).toEqual(["a.mp4"]);
    expect(r.skipped).toEqual(["b.mxf"]);
  });
});

describe("folderPrefix", () => {
  it("끝에 슬래시를 붙이고 맨 앞 슬래시를 뗀다", () => {
    expect(folderPrefix(" /news/2026 ")).toBe("news/2026/");
    expect(folderPrefix("news/2026/")).toBe("news/2026/");
  });

  it("버킷 전체는 보내지 않는다", () => {
    expect(folderPrefix("")).toBe("");
    expect(folderPrefix(" / ")).toBe("");
  });
});

describe("missingKeyOf", () => {
  it("저장소에 없는 키 오류에서 키를 꺼낸다", () => {
    expect(missingKeyOf("storage_key_not_found:news/typo.mp4")).toBe("news/typo.mp4");
  });

  it("다른 오류나 문자열이 아닌 값은 null이다", () => {
    expect(missingKeyOf("system_prefix")).toBeNull();
    expect(missingKeyOf(undefined)).toBeNull();
    expect(missingKeyOf(["x"])).toBeNull();
  });
});
