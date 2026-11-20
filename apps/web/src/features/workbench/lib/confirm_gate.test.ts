/** 확인 창 실행 잠금 검사. */
import { describe, expect, it } from "vitest";

import { createConfirmGate } from "./confirm_gate";

describe("createConfirmGate", () => {
  it("같은 틱의 두 번째 실행은 들어가지 못한다 — 더블클릭이 요청 둘을 보내던 자리다", () => {
    const gate = createConfirmGate();
    const sent: number[] = [];
    const click = (n: number) => { if (gate.tryEnter()) sent.push(n); };
    click(1);
    click(2);
    expect(sent).toEqual([1]);
  });

  it("풀면 다시 들어간다 — 실패한 실행은 창에서 다시 누를 수 있다", () => {
    const gate = createConfirmGate();
    expect(gate.tryEnter()).toBe(true);
    gate.release();
    expect(gate.tryEnter()).toBe(true);
  });

  it("잠금마다 따로다 — 다른 창의 실행을 막지 않는다", () => {
    const a = createConfirmGate();
    const b = createConfirmGate();
    expect(a.tryEnter()).toBe(true);
    expect(b.tryEnter()).toBe(true);
  });
});
