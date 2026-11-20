/** 클립보드 복사 테스트. 평문 http 주소에서는 `navigator.clipboard`가 없어도 복사해야 한다. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { copyText } from "./clipboard";

afterEach(() => vi.unstubAllGlobals());

/** 테스트용 문서. 만든 입력란과 `execCommand` 호출을 기록한다. */
function fakeDocument(execResult: boolean | "throw") {
  const made: { value: string; attached: boolean; removed: boolean }[] = [];
  const calls: string[] = [];
  let selected = "";
  const body = { appendChild: (el: { attached: boolean }) => { el.attached = true; } };
  const doc = {
    activeElement: null,
    body,
    createElement: () => {
      const el = {
        value: "", attached: false, removed: false, style: {},
        setAttribute: () => {},
        select() { selected = el.value; },
        setSelectionRange: () => {},
        remove() { el.removed = true; },
      };
      made.push(el);
      return el;
    },
    execCommand: (cmd: string) => {
      calls.push(`${cmd}:${selected}`);
      if (execResult === "throw") throw new Error("denied");
      return execResult;
    },
  };
  return { doc, made, calls };
}

describe("copyText", () => {
  it("클립보드 API가 있으면 그것으로 복사한다", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await copyText("abc");
    expect(writeText).toHaveBeenCalledWith("abc");
  });
  it("클립보드 API가 없으면 선택한 입력란으로 복사하고 입력란을 지운다", async () => {
    const { doc, made, calls } = fakeDocument(true);
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("document", doc);
    await copyText("http://10.0.0.1/password-reset?token=x");
    expect(calls).toEqual(["copy:http://10.0.0.1/password-reset?token=x"]);
    expect(made).toHaveLength(1);
    expect(made[0].attached && made[0].removed).toBe(true);
  });
  it("클립보드 API가 거절하면 입력란 방식으로 한 번 더 시도한다", async () => {
    const { doc, calls } = fakeDocument(true);
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("NotAllowed")) } });
    vi.stubGlobal("document", doc);
    await copyText("id-1");
    expect(calls).toEqual(["copy:id-1"]);
  });
  it("두 방법이 모두 실패하면 거부해 호출부가 실패를 알리게 한다", async () => {
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("document", fakeDocument(false).doc);
    await expect(copyText("x")).rejects.toThrow();
    vi.stubGlobal("document", fakeDocument("throw").doc);
    await expect(copyText("x")).rejects.toThrow();
  });
});
