/** Escape로 닫을 때 포커스를 돌려줄지 판정하는 `focusWouldBeLost`를 검증한다. */
import { describe, expect, it } from "vitest";

import { focusWouldBeLost } from "./useOutsideClose";

/** 자식 목록으로 `contains`를 흉내 내는 노드를 만든다. */
function node(...kids: object[]) {
  const self = { contains: (o: unknown) => o === self || kids.includes(o as object) };
  return self;
}

describe("focusWouldBeLost", () => {
  const body = node();
  const input = node();
  const menu = node(input);
  const elsewhere = node();

  it("포커스가 닫히는 메뉴 안에 있으면 잃는다", () => {
    expect(focusWouldBeLost(input, body, [menu])).toBe(true);
  });
  it("포커스가 없거나 body면 잃은 것이다", () => {
    expect(focusWouldBeLost(null, body, [menu])).toBe(true);
    expect(focusWouldBeLost(body, body, [menu])).toBe(true);
  });
  it("메뉴 밖으로 사용자가 옮긴 포커스는 건드리지 않는다", () => {
    expect(focusWouldBeLost(elsewhere, body, [menu])).toBe(false);
  });
  it("포털로 띄운 메뉴(extraRef)도 메뉴로 본다", () => {
    expect(focusWouldBeLost(input, body, [node(), menu])).toBe(true);
    expect(focusWouldBeLost(input, body, [null, undefined])).toBe(false);
  });
});
