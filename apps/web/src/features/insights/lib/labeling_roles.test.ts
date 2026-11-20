/**
 * 사람별 라벨링 표에 실을 행을 검사한다.
 *
 * 라벨링하지 않는 멤버는 기본으로 숨긴다.
 */
import { describe, expect, it } from "vitest";

import { doesLabeling, labelingRows } from "./labeling_roles";

/** 검사에 쓰는 다섯 행. 라벨러 둘, 관리자, 검수자, 멤버 아닌 제출자다. */
const ROWS = [
  { name: "김라벨", role: "annotator", submitted: 18 },
  { name: "정신입", role: "annotator", submitted: 0 },
  { name: "관리자", role: "project_admin", submitted: 1 },
  { name: "박검수", role: "reviewer", submitted: 0 },
  { name: "Super Admin", role: null, submitted: 16 },
];

describe("doesLabeling", () => {
  it("라벨러와 관리자는 제출이 0이어도 라벨링하는 사람이다", () => {
    expect(doesLabeling({ role: "annotator", submitted: 0 })).toBe(true);
    expect(doesLabeling({ role: "project_admin", submitted: 0 })).toBe(true);
  });

  it("검수자는 제출이 없으면 라벨링하지 않는다", () => {
    expect(doesLabeling({ role: "reviewer", submitted: 0 })).toBe(false);
  });

  it("제출이 없어도 기간 안에 판정받았으면 라벨링하는 사람이다", () => {
    // 나간 멤버가 기간 전에 낸 작업이 기간 안에 판정된 경우다. 숨기면 표의 판정 합이 요약 칸보다 작아진다.
    expect(doesLabeling({ role: null, submitted: 0, approval_base: 1 })).toBe(true);
    expect(doesLabeling({ role: "reviewer", submitted: 0, approval_base: 0 })).toBe(false);
  });

  it("역할과 관계없이 제출이 있으면 라벨링하는 사람이다 — 멤버가 아닌 제출자도", () => {
    expect(doesLabeling({ role: "reviewer", submitted: 2 })).toBe(true);
    expect(doesLabeling({ role: null, submitted: 16 })).toBe(true);
  });
});

describe("labelingRows", () => {
  it("기본은 라벨링하는 사람만 싣고 숨긴 수를 센다", () => {
    const { shown, others } = labelingRows(ROWS, false);
    expect(shown.map((r) => r.name)).toEqual(["김라벨", "정신입", "관리자", "Super Admin"]);
    expect(others).toBe(1);
  });

  it("모두 보기면 전부 싣고, 숨긴 수는 그대로 센다", () => {
    const { shown, others } = labelingRows(ROWS, true);
    expect(shown).toHaveLength(5);
    expect(others).toBe(1);
  });
});
