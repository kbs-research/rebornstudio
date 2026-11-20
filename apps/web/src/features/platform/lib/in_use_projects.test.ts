import { describe, expect, it } from "vitest";

import { inUseProjectsLine } from "./in_use_projects";

describe("inUseProjectsLine", () => {
  it("이름을 쉼표로 잇는다", () => {
    expect(inUseProjectsLine({ names: ["뉴스 A", "뉴스 B"], more: 0 })).toBe("사용 중인 프로젝트: 뉴스 A, 뉴스 B");
  });

  it("서버가 싣지 않은 나머지는 수로 적는다", () => {
    expect(inUseProjectsLine({ names: ["뉴스 A"], more: 1200 })).toBe("사용 중인 프로젝트: 뉴스 A 외 1,200개");
  });
});
