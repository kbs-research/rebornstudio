/**
 * 승인율·반려율 칸의 표시 검사. 판정 표(`packages/reborn-contracts/labeler-rates.json`)의 사례를 그대로 돌린다.
 *
 * 서버 쪽 짝은 `apps/api-gateway/tests/contract/test_labeler_rates.py`다.
 * 서버는 표의 `counts`로 `row`를 내고, 이 테스트는 같은 `row`로 화면 값(`view`)을 확인한다.
 * 한쪽만 통과하면 비율과 괄호가 다른 수를 가리키는 상태다.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { approvalView, rejectionPct, rejectionView, type RateView } from "./rates";

type Case = {
  counts: { submitted: number; approved: number; rejected: number };
  row: {
    approval_rate: number | null;
    approval_base: number;
    rejection_rate: number | null;
    rejection_base: number;
  };
  view: { approval: RateView; rejection: RateView };
  why: string;
};

const vectors = JSON.parse(
  readFileSync(
    resolve(
      fileURLToPath(new URL(".", import.meta.url)),
      "../../../../../../packages/reborn-contracts/labeler-rates.json",
    ),
    "utf-8",
  ),
) as { cases: Case[] };

describe("판정 표가 비어 있지 않다", () => {
  it("비면 아래 시험들이 0건을 통과한다", () => {
    expect(vectors.cases.length).toBeGreaterThanOrEqual(7);
  });
});

describe("판정 표의 행을 같은 값으로 그린다", () => {
  for (const c of vectors.cases) {
    const row = { ...c.counts, ...c.row };
    it(`승인율: ${c.why}`, () => {
      expect(approvalView(row)).toEqual(c.view.approval);
    });
    it(`반려율: ${c.why}`, () => {
      expect(rejectionView(row)).toEqual(c.view.rejection);
    });
  }
});

describe("괄호는 서버 분모를 쓴다", () => {
  it("제출 0건에 반려 18건이면 (18/0)이 아니라 (18/18)", () => {
    const v = rejectionView({ rejected: 18, rejection_rate: 1, rejection_base: 18 });
    expect(v.sub).toBe("(18/18)");
    expect(v.sub).not.toContain("/0)");
  });

  it("어느 사례도 값이 있는데 분모가 0인 괄호를 쓰지 않는다", () => {
    for (const c of vectors.cases) {
      const row = { ...c.counts, ...c.row };
      for (const v of [approvalView(row), rejectionView(row)]) {
        if (v.kind === "rate") expect(v.sub).not.toMatch(/\/0\)$/);
      }
    }
  });
});

describe("rejectionPct", () => {
  it("소수 1자리 퍼센트로 바꾸고 값이 없으면 null", () => {
    expect(rejectionPct(2 / 7)).toBe(28.6);
    expect(rejectionPct(0)).toBe(0);
    expect(rejectionPct(null)).toBeNull();
  });
});
