/** UUID 테스트. 평문 http 주소에서도 등록 묶음 id를 만들고, 주소의 id 모양을 거르는지 본다. */
import { webcrypto } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";

import { isUuid, randomUuid } from "./uuid";

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => vi.unstubAllGlobals());

describe("randomUuid", () => {
  it("보안 컨텍스트에서는 v4 형식을 돌려준다", () => {
    expect(randomUuid()).toMatch(V4);
  });
  it("randomUUID가 없는 주소에서도 getRandomValues로 v4 형식을 만든다", () => {
    // 평문 http 주소의 브라우저와 같은 모양이다. getRandomValues만 있고 randomUUID·subtle은 없다.
    vi.stubGlobal("crypto", { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) });
    const ids = Array.from({ length: 200 }, () => randomUuid());
    for (const id of ids) expect(id).toMatch(V4);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("isUuid — 주소의 id 모양", () => {
  it("UUID 모양만 받는다", () => {
    expect(isUuid("035f0b26-1c2d-4e5f-8a9b-0c1d2e3f4a5b")).toBe(true);
    expect(isUuid("not-a-uuid")).toBe(false);
    expect(isUuid("")).toBe(false);
    expect(isUuid(undefined)).toBe(false);
  });
});
