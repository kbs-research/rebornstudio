import { describe, expect, it } from "vitest";

import { requestProjectSwitcher, useProjectSwitcherRequest } from "./project";

describe("requestProjectSwitcher", () => {
  it("부를 때마다 신호 값이 바뀐다. 이미 열어 달라고 한 뒤에도 다시 열 수 있다", () => {
    const before = useProjectSwitcherRequest.getState().nonce;
    requestProjectSwitcher();
    requestProjectSwitcher();
    expect(useProjectSwitcherRequest.getState().nonce).toBe(before + 2);
  });
});
