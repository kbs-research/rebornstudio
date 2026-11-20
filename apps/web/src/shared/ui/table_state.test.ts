import { describe, expect, it } from "vitest";

import { clampPage, readTableUrlState, tableCountLabel, writeTableUrlState } from "./table_state";

describe("tableCountLabel", () => {
  it("한 페이지에 다 보이면 같은 수를 두 번 쓰지 않는다", () => {
    expect(tableCountLabel({ base: 10, matched: 10, from: 1, to: 10, unit: "건" })).toBe("전체 10건");
  });
  it("여러 페이지면 이 페이지의 범위를 쓴다", () => {
    expect(tableCountLabel({ base: 39, matched: 39, from: 26, to: 39, unit: "명" })).toBe("39명 중 26–39");
  });
  it("미리 거른 목록은 전체 수를 분모로 쓴다", () => {
    expect(tableCountLabel({ base: 13, matched: 6, from: 1, to: 6, unit: "개" })).toBe("13개 중 6개");
  });
  it("거른 목록이 여러 페이지면 범위를 붙인다", () => {
    expect(tableCountLabel({ base: 100, matched: 30, from: 26, to: 30, unit: "명" })).toBe("100명 중 30명 · 26–30");
  });
});

describe("표 상태 주소", () => {
  it("쓴 것을 그대로 읽는다", () => {
    const s = { sort: { label: "플랫폼 역할", dir: "desc" as const }, q: "QA", page: 2, rows: 50 };
    const sp = writeTableUrlState(new URLSearchParams("tab=all"), "", s);
    expect(sp.get("tab")).toBe("all");
    expect(readTableUrlState(sp, "")).toEqual(s);
  });
  it("기본값은 주소에 적지 않는다", () => {
    const sp = writeTableUrlState(new URLSearchParams(), "u", { sort: null, q: "", page: 1, rows: null });
    expect(sp.toString()).toBe("");
  });
  it("모르는 값은 버린다", () => {
    const st = readTableUrlState(new URLSearchParams("page=-3&rows=7&dir=sideways&sort=이름"), "");
    expect(st.page).toBe(1);
    expect(st.rows).toBeNull();
    expect(st.sort).toEqual({ label: "이름", dir: "asc" });
  });
  it("접두어가 다른 표의 키는 건드리지 않는다", () => {
    const sp = writeTableUrlState(new URLSearchParams("a.q=x&a.page=3"), "b", { sort: null, q: "y", page: 1, rows: null });
    expect(sp.get("a.q")).toBe("x");
    expect(sp.get("a.page")).toBe("3");
    expect(sp.get("b.q")).toBe("y");
  });
});

describe("clampPage", () => {
  it("범위 밖 쪽은 마지막 쪽으로 맞춘다. 주소도 이 값으로 적어 보이는 쪽과 같게 한다", () => {
    expect(clampPage(99, 59, 50)).toBe(2);
    expect(clampPage(3, 59, 25)).toBe(3);
    expect(clampPage(4, 50, 25)).toBe(2);
  });
  it("행이 아직 없거나 페이지를 끈 표는 쪽을 바꾸지 않는다. 받기 전에 주소의 쪽을 지우지 않는다", () => {
    expect(clampPage(2, 0, 25)).toBe(2);
    expect(clampPage(5, 120, 0)).toBe(5);
  });
});
