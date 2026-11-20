/** 수정률 표시 값 고르기와 정의·100% 초과 설명을 검사한다. */
import { describe, expect, it } from "vitest";

import {
  EDIT_RATE_OVER_NOTE,
  EDIT_RATE_UNMEASURED,
  editRateDefine,
  editRateFraction,
  editRateNote,
  editRateOver,
  editRateView,
} from "./edit_rate";

describe("editRateView — 서버 수를 읽고 셋 중 무엇을 그릴지만 고른다", () => {
  it("잰 제출이 있으면 서버 비율과 잰 건수를 그대로", () => {
    expect(editRateView({ submitted: 10, edit_rate: 12.4, edit_measured: 8 })).toEqual({
      kind: "rate",
      text: "12.4%",
      measured: 8,
    });
  });

  it("0%는 값이다 — 잰 제출에서 한 글자도 안 고쳤다", () => {
    expect(editRateView({ submitted: 3, edit_rate: 0, edit_measured: 3 })).toEqual({
      kind: "rate",
      text: "0%",
      measured: 3,
    });
  });

  it("낸 것은 있는데 잰 것이 없으면 측정 전 — 0%로 그리지 않는다", () => {
    expect(editRateView({ submitted: 5, edit_rate: null, edit_measured: 0 })).toEqual({
      kind: "unmeasured",
      text: EDIT_RATE_UNMEASURED,
    });
  });

  it("낸 것이 없으면 옆 비율 칸과 같은 자리표", () => {
    expect(editRateView({ submitted: 0, edit_rate: null, edit_measured: 0 })).toEqual({
      kind: "none",
      text: "—",
    });
  });

  it("100%를 넘는 값도 자르지 않는다 — 초안보다 길게 새로 쓴 경우", () => {
    expect(editRateView({ submitted: 1, edit_rate: 250, edit_measured: 1 }).text).toBe("250%");
  });

  it("옛 응답처럼 칸이 없어도 측정 전으로 읽는다", () => {
    expect(editRateView({ submitted: 2 }).kind).toBe("unmeasured");
  });
});

describe("editRateDefine", () => {
  it("기간 이름을 넣고, 무엇을 안 세는지와 측정 전을 말한다", () => {
    const t = editRateDefine("최근 7일");
    expect(t).toContain("최근 7일 안의 제출을 셉니다");
    expect(t).toContain("작업별 첫 제출만 세고 반려 후 다시 제출한 것은 세지 않습니다");
    expect(t).toContain("측정 전");
  });

  it("뜻을 먼저 쓰고 계산식은 괄호로 맨 뒤에 붙인다", () => {
    const t = editRateDefine("전체 기간");
    expect(t.startsWith("AI 초안의 글자 수 대비 교정한 글자 수의 비율입니다.")).toBe(true);
    expect(t.endsWith("(교정한 글자 ÷ 초안 글자)")).toBe(true);
    expect(t).not.toMatch(/분모|등급/);
  });
});

describe("editRateOver", () => {
  it("100%를 넘을 때만 참이다 — 100%는 초안 글자만큼 고친 것이라 설명이 필요 없다", () => {
    expect(editRateOver(718.6)).toBe(true);
    expect(editRateOver(100)).toBe(false);
    expect(editRateOver(12.4)).toBe(false);
    expect(editRateOver(null)).toBe(false);
  });
});

describe("editRateFraction·editRateNote", () => {
  const row = { edit_distance: 1215, draft_chars: 1000, edit_measured: 42, edit_rate: 121.5 };

  it("분자와 분모를 글자 수로 쓴다", () => {
    expect(editRateFraction(row)).toBe("(1,215/1,000자)");
  });

  it("100%를 넘으면 분자·분모와 함께 넘는 이유를 쓴다", () => {
    const t = editRateNote(row);
    expect(t).toContain("교정한 글자 1,215자를 AI 초안 글자 1,000자로 나눈 값입니다.");
    expect(t).toContain("측정한 제출은 42건입니다.");
    expect(t).toContain(EDIT_RATE_OVER_NOTE);
  });

  it("100% 이하면 넘는 이유를 붙이지 않는다", () => {
    expect(editRateNote({ ...row, edit_distance: 124, edit_rate: 12.4 })).not.toContain(EDIT_RATE_OVER_NOTE);
  });
});
