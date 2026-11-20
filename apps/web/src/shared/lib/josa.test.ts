/** 조사 선택 테스트. 괄호 나열(`을(를)`) 대신 받침으로 고르고, 영문·숫자는 읽는 소리로 본다. */
import { describe, expect, it } from "vitest";

import { eulReul, hasFinalConsonant, withJosa } from "./josa";

describe("eulReul", () => {
  it("한글 받침", () => {
    expect(eulReul("김라벨")).toBe("김라벨을");
    expect(eulReul("정보라")).toBe("정보라를");
  });
  it("영문은 l·m·n만 받침으로 읽는다", () => {
    expect(eulReul("Default Project")).toBe("Default Project를");
    expect(eulReul("Super Admin")).toBe("Super Admin을");
  });
});

describe("hasFinalConsonant", () => {
  it("닫는 따옴표를 걷어내고 본다", () => {
    expect(hasFinalConsonant('"프로젝트"')).toBe(false);
  });
  it("판정할 수 없으면 받침 있는 쪽", () => {
    expect(hasFinalConsonant("")).toBe(true);
    expect(hasFinalConsonant(null)).toBe(true);
  });
  it("-ng는 ㅇ 받침, 숫자는 읽기 끝소리", () => {
    expect(withJosa("marketing", "이", "가")).toBe("marketing이");
    expect(withJosa("3", "이", "가")).toBe("3이");
    expect(withJosa("2", "이", "가")).toBe("2가");
  });
});
