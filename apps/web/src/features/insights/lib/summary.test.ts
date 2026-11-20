/**
 * 통계 요약 칸의 표시 검사. 비율 칸이 사람 표와 같은 함수로 값과 괄호를 고르는지 본다.
 *
 * 합계와 분모는 서버가 낸 값을 그대로 쓴다. 0으로 나눈 괄호(`(0/0)`)가 어느 칸에도 나오지 않아야 한다.
 */
import { describe, expect, it } from "vitest";

import type { LabelerPerfSummary } from "@/shared/api/client";

import { approvalView, rejectionView } from "./rates";
import { summaryCells } from "./summary";

/** 수가 하나도 없는 합계. */
const EMPTY: LabelerPerfSummary = {
  submitted: 0, submitters: 0, approved: 0, rejected: 0,
  approval_rate: null, rejection_rate: null, approval_base: 0, rejection_base: 0,
  edit_rate: null, edit_measured: 0, edit_distance: 0, draft_chars: 0,
};

/** 칸 이름으로 칸 하나를 찾는다. */
function cell(s: LabelerPerfSummary, key: string) {
  const c = summaryCells(s, "최근 30일").find((x) => x.key === key);
  if (!c) throw new Error(`칸 없음: ${key}`);
  return c;
}

describe("summaryCells", () => {
  it("칸은 다섯 개이고 이름은 지표 사전의 이름이다", () => {
    expect(summaryCells(EMPTY, "전체 기간").map((c) => c.name)).toEqual(["제출", "판정", "승인율", "반려율", "수정률"]);
  });

  it("보통 합계는 사람 표와 같은 함수로 값과 괄호를 고른다", () => {
    const s: LabelerPerfSummary = {
      ...EMPTY,
      submitted: 1234, submitters: 12, approved: 5, rejected: 2,
      approval_rate: 71.4, approval_base: 7, rejection_rate: 2 / 1234, rejection_base: 1234,
      edit_rate: 12.4, edit_measured: 6, edit_distance: 1240, draft_chars: 10000,
    };
    expect(cell(s, "submitted")).toMatchObject({ value: "1,234", unit: "건", sub: "제출한 사람 12명" });
    expect(cell(s, "decided")).toMatchObject({ value: "7", unit: "건", sub: "승인 5 · 반려 2" });
    const a = approvalView(s);
    expect(cell(s, "approvalRate")).toMatchObject({ value: "71.4", unit: "%", sub: a.sub });
    expect(a.sub).toBe("(5/7)");
    const r = rejectionView(s);
    expect(cell(s, "rejectionRate")).toMatchObject({ value: "0.2", unit: "%", sub: r.sub });
    expect(r.sub).toBe("(2/1,234)");
    expect(cell(s, "editRate")).toMatchObject({ value: "12.4", unit: "%", sub: "(1,240/10,000자)" });
  });

  it("제출이 없으면 비율 칸은 —이고 단위를 붙이지 않는다", () => {
    const cells = summaryCells(EMPTY, "전체 기간");
    for (const key of ["approvalRate", "rejectionRate", "editRate"]) {
      expect(cell(EMPTY, key)).toMatchObject({ value: "—", unit: undefined });
    }
    expect(cell(EMPTY, "submitted")).toMatchObject({ value: "0", sub: "제출 없음" });
    expect(cell(EMPTY, "decided")).toMatchObject({ value: "0", sub: "판정 없음" });
    expect(cells.some((c) => c.sub.includes("/0)"))).toBe(false);
  });

  it("제출은 있는데 판정이 없으면 승인율은 측정 전이고 반려율은 0%(0/제출)이다", () => {
    const s: LabelerPerfSummary = {
      ...EMPTY, submitted: 5, submitters: 2, rejection_rate: 0, rejection_base: 5,
    };
    expect(cell(s, "approvalRate")).toMatchObject({ value: "측정 전", unit: undefined, sub: "판정 없음" });
    expect(cell(s, "rejectionRate")).toMatchObject({ value: "0", unit: "%", sub: "(0/5)" });
    expect(cell(s, "editRate")).toMatchObject({ value: "측정 전", sub: "측정한 제출 없음" });
  });

  it("판정만 있고 제출이 없는 기간은 판정 수를 분모로 쓴다", () => {
    const s: LabelerPerfSummary = {
      ...EMPTY, approved: 1, rejected: 1, approval_rate: 50, approval_base: 2,
      rejection_rate: 0.5, rejection_base: 2,
    };
    expect(cell(s, "submitted")).toMatchObject({ value: "0", sub: "제출 없음" });
    expect(cell(s, "approvalRate")).toMatchObject({ value: "50", sub: "(1/2)" });
    expect(cell(s, "rejectionRate")).toMatchObject({ value: "50", sub: "(1/2)" });
    expect(cell(s, "editRate")).toMatchObject({ value: "—", sub: "제출 없음" });
  });

  it("정의 문구는 기간 이름과 표의 합과 같다는 문장을 담는다", () => {
    const c = cell(EMPTY, "submitted");
    expect(c.define).toContain("최근 30일 안의 프로젝트 전체 제출을 셉니다.");
    expect(c.define).toContain("아래 사람별 표의 수를 더한 값과 같습니다.");
  });
});
