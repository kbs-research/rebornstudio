/** 개요 `조치 필요` 카드의 행 나누기와 문장, 개요 카드 배치를 검사한다. */
import { describe, expect, it } from "vitest";

import { blockerHint, noneLine, overviewLayout, splitActionRows, workerOverviewLayout, type ActionRow } from "./action_rows";

/** 테스트용 행을 만든다. */
const row = (key: string, count: number | null): ActionRow => ({
  key, label: key, hint: "", count, unit: "건", to: "", toLabel: "", tone: "warn",
});

describe("noneLine", () => {
  it("대상을 먼저 쓰고 없음을 끝에 둔다", () => {
    expect(noneLine([{ label: "캡션 품질검수 보류" }, { label: "끝나지 않은 문의" }])).toBe(
      "캡션 품질검수 보류·끝나지 않은 문의 없음",
    );
  });
  it("이름은 셋까지 잇는다", () => {
    expect(noneLine([{ label: "a" }, { label: "b" }, { label: "c" }], 6)).toBe("a·b·c 없음");
  });
  it("넷부터는 앞의 둘과 나머지 수로 줄인다", () => {
    const rows = ["반복 반려", "장기 검수 대기", "c", "d", "e"].map((label) => ({ label }));
    expect(noneLine(rows, 7)).toBe("반복 반려·장기 검수 대기 외 3개 항목 없음");
  });
  it("검사한 항목이 전부 0건이면 이름 없이 한 줄로 쓴다", () => {
    const rows = ["a", "b"].map((label) => ({ label }));
    expect(noneLine(rows, 2)).toBe("조치 필요 항목 없음");
  });
});

describe("splitActionRows", () => {
  it("0건만 없음 줄로 보내고 세지 못한 행은 표에 남긴다", () => {
    const { shown, none } = splitActionRows([row("a", 2), row("b", 0), row("c", null)]);
    expect(shown.map((r) => r.key)).toEqual(["a", "c"]);
    expect(none.map((r) => r.key)).toEqual(["b"]);
  });
});

describe("blockerHint", () => {
  it("사유 이름 뒤에 항목을 붙여 수와 구별한다", () => {
    expect(blockerHint({ 캡션: 92 }, 94)).toBe("승인 94건 중 · 캡션 항목 92건");
  });
  it("승인 수를 모르면 사유만 쓴다", () => {
    expect(blockerHint({ 검수: 1200 }, null)).toBe("검수 항목 1,200건");
  });
});

describe("overviewLayout", () => {
  it("작업 가이드가 있으면 행 수와 관계없이 조치 필요를 맨 위에 둔다", () => {
    expect(overviewLayout(0, true)).toBe("top");
    expect(overviewLayout(3, true)).toBe("top");
    expect(overviewLayout(6, true)).toBe("top");
    expect(overviewLayout(null, true)).toBe("top");
  });
  it("작업 가이드가 없고 행이 둘 이하이면 조치 필요와 가이드 없음을 왼쪽 열에 둔다", () => {
    expect(overviewLayout(0, false)).toBe("left");
    expect(overviewLayout(2, false)).toBe("left");
  });
  it("작업 가이드가 없고 행이 셋 이상이면 가이드 없음을 진척률 아래에 둔다", () => {
    expect(overviewLayout(3, false)).toBe("side");
    expect(overviewLayout(6, false)).toBe("side");
  });
  it("아직 계산하지 못했으면 행이 적은 경우와 같은 배치를 쓴다", () => {
    expect(overviewLayout(null, false)).toBe("left");
  });
});

describe("workerOverviewLayout", () => {
  it("진척률을 보지 않으면 가이드 유무와 관계없이 한 열로 둔다", () => {
    expect(workerOverviewLayout(false, true)).toBe("single");
    expect(workerOverviewLayout(false, false)).toBe("single");
  });
  it("작업 가이드가 있으면 진척률을 이어서 할 작업 옆에 두고 가이드를 아래 전체 폭에 둔다", () => {
    expect(workerOverviewLayout(true, true)).toBe("top");
  });
  it("작업 가이드가 없으면 가이드 없음을 이어서 할 작업 아래에 둔다", () => {
    expect(workerOverviewLayout(true, false)).toBe("left");
  });
  it("관리자 개요가 짧은 위 카드를 둘 때와 같은 배치를 고른다", () => {
    expect(workerOverviewLayout(true, true)).toBe(overviewLayout(0, true));
    expect(workerOverviewLayout(true, false)).toBe(overviewLayout(0, false));
  });
});
