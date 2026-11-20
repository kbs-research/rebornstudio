/**
 * 지표 사전을 검사한다.
 *
 * 이름이 용어집과 같은지, 정의가 뜻을 먼저 쓰고 계산식을 뒤에 두는지 확인한다.
 *
 * 이름이 달라지면 통계 표와 사람 서랍이 같은 수를 다른 이름으로 부른다.
 * 그래서 용어집의 이름을 테스트로 고정한다.
 */
import { describe, expect, it } from "vitest";

import { fmtFraction, metricDefine, METRICS } from "./metrics";

describe("METRICS 이름", () => {
  it("사람 지표는 용어집의 이름을 쓴다", () => {
    expect(METRICS.submitted.name).toBe("제출");
    expect(METRICS.approvalRate.name).toBe("승인율");
    expect(METRICS.rejectionRate.name).toBe("반려율");
    expect(METRICS.editRate.name).toBe("수정률");
    expect(METRICS.holding.name).toBe("보유 중");
  });

  it("쓰지 않기로 한 이름이 없다", () => {
    const names = Object.values(METRICS).map((m) => m.name);
    for (const banned of ["처리량", "통과율", "현재 보유", "평균 소요"]) {
      expect(names).not.toContain(banned);
    }
  });

  it("정의는 한 문장으로 시작하고 ~입니다.로 끝난다", () => {
    for (const m of Object.values(METRICS)) {
      expect(m.meaning.endsWith("입니다.")).toBe(true);
      // 정의에 통계 용어를 쓰지 않는다.
      expect(`${m.meaning} ${(m.notes ?? []).join(" ")}`).not.toMatch(/분모|분자/);
    }
  });
});

describe("metricDefine", () => {
  it("뜻을 먼저 쓰고 계산식은 괄호로 맨 뒤에 붙인다", () => {
    const d = metricDefine(METRICS.approvalRate, "최근 7일 안의 판정만 셉니다.");
    expect(d.startsWith(METRICS.approvalRate.meaning)).toBe(true);
    expect(d.endsWith("(승인 ÷ (승인 + 반려))")).toBe(true);
    expect(d).toContain("최근 7일 안의 판정만 셉니다.");
  });

  it("계산식이 없으면 괄호도 없다", () => {
    expect(metricDefine(METRICS.submitted)).toBe(
      "라벨링 작업을 제출한 건수입니다. 반려 후 다시 제출하면 따로 셉니다.",
    );
  });

  it("빈 설명은 건너뛴다", () => {
    expect(metricDefine(METRICS.submitted, false, null, undefined, "")).toBe(
      metricDefine(METRICS.submitted),
    );
  });
});

describe("분자·분모 표기", () => {
  it("큰 수는 천 단위로 끊는다", () => {
    expect(fmtFraction(1234, 5678)).toBe("(1,234/5,678)");
  });
});
