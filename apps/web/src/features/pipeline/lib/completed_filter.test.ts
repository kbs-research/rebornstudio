/**
 * 완료 목록 거르기 축 사전이 주소 값을 안전하게 읽는지 확인한다.
 *
 * 이 테스트는 사전에 없는 값이 서버로 넘어가지 않는 것을 보장한다.
 * 북마크, 손으로 고친 주소, 옛 링크는 `?filter=`에 어떤 값이든 실어 올 수 있다.
 * 그 값을 그대로 보내면 서버가 422를 돌려주고 목록 화면 전체가 오류가 된다.
 * 화면에 축을 고르는 컨트롤이 있으므로 오류 대신 전체 목록을 보여 준다.
 */
import { describe, expect, it } from "vitest";

import { COMPLETED_FILTERS, completedFilterLabel, completedFilterOf, visibleCompletedFilters } from "./completed_filter";

describe("completedFilterOf", () => {
  it("아는 값은 그대로 읽는다", () => {
    for (const { value } of COMPLETED_FILTERS) {
      expect(completedFilterOf(value)).toBe(value);
    }
  });

  it("모르는 값·없는 값은 전체로 떨어진다 — 오류 화면 대신", () => {
    expect(completedFilterOf(null)).toBe("all");
    expect(completedFilterOf("")).toBe("all");
    expect(completedFilterOf("rejected")).toBe("all");     // 클립 상태 쪽 낱말
    expect(completedFilterOf("DELIVERABLE")).toBe("all");  // 대소문자가 다르면 다른 값이다
  });
});

describe("completedFilterLabel", () => {
  it("축마다 이름이 있다 — 표 아래 분모와 빈 상태가 같은 말을 쓴다", () => {
    expect(completedFilterLabel("all")).toBe("전체");
    expect(completedFilterLabel("deliverable")).toBe("납품 대상");
    expect(completedFilterLabel("excluded")).toBe("납품 불가");
    expect(completedFilterLabel("qc_rules")).toBe("규칙만 검사");
  });

  it("이름은 서로 달라야 한다 — 같으면 세그먼트에서 둘을 못 가린다", () => {
    const labels = COMPLETED_FILTERS.map((f) => f.label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  /* `제외`는 클립 상태(비식별화 신고로 뺀 클립)가 이미 쓰는 말이다.
     그래서 납품 축은 `불가`로 구분해 부른다.
     이 테스트는 납품 축 이름이 `제외`로 되돌아가지 않게 보장한다. */
  it("납품 축은 제외가 아니라 불가로 부른다", () => {
    expect(completedFilterLabel("excluded")).not.toContain("제외");
  });
});

describe("visibleCompletedFilters", () => {
  it("품질검수를 끈 프로젝트에는 규칙만 검사 축이 없다 — 늘 0인 칩을 두지 않는다", () => {
    expect(visibleCompletedFilters({ qcEnabled: false, qcRulesCount: 0 }))
      .toEqual(["all", "deliverable", "excluded"]);
  });

  it("켜 있던 동안 규칙만 검사한 완료분이 남았으면 꺼도 축을 둔다 — 그 건을 찾을 길을 지우지 않는다", () => {
    expect(visibleCompletedFilters({ qcEnabled: false, qcRulesCount: 3 }))
      .toContain("qc_rules");
  });

  it("품질검수를 켠 프로젝트는 0이어도 축을 둔다", () => {
    expect(visibleCompletedFilters({ qcEnabled: true, qcRulesCount: 0 }))
      .toEqual(["all", "deliverable", "excluded", "qc_rules"]);
  });
});
