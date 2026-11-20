/**
 * `progressView` 테스트. 개요와 통계가 함께 쓰는 분기 로직이 상태마다 무엇을 내는지 검증한다.
 *
 * 분기 로직을 한 함수에 모아 두었으므로 "시간 못 잰 0은 그리지 않는다"·"작업 0건이면 %를 그리지 않는다" 같은 규칙을
 * 한 번에 확인할 수 있다.
 */
import { describe, expect, it } from "vitest";

import {
  COUNT_BASIS_DEFINE,
  HOURS_BASIS_DEFINE,
  progressView,
  WORK_POPULATION_DEFINE,
} from "./progress";
import type { BusinessKpi } from "@/shared/api/client";

/** 서버 응답 한 건을 만든다. 테스트가 보는 필드만 채우고 나머지는 기본값이다. */
function kpi(over: Partial<BusinessKpi> = {}): BusinessKpi {
  return {
    done_count: 0,
    total_count: 0,
    progress_pct: 0,
    done_hours: 0,
    total_hours: 0,
    hours_unmeasurable_count: 0,
    ...over,
  } as BusinessKpi;
}

describe("progressView", () => {
  it("응답이 없으면 큰 수는 —다 (0%가 아니다)", () => {
    const v = progressView(undefined);
    expect(v.headline).toBe("—");
    expect(v.basis).toBeNull();
    expect(v.note).toBeNull();
  });

  it("작업이 0건이면 %를 안 그리고 측정 전이라 쓴다", () => {
    const v = progressView(kpi({ total_count: 0, progress_pct: 0 }));
    expect(v.headline).toBe("—");
    expect(v.note).toBe("진척 측정 전");
    expect(v.count).toBeNull();
  });

  it("시간을 못 재면 건수가 큰 수이고, 아래 건수 줄은 두지 않는다", () => {
    const v = progressView(kpi({ done_count: 12, total_count: 39, progress_pct: 30.8 }));
    expect(v.headline).toBe("30.8%");
    expect(v.basis?.label).toBe("건수 기준");
    expect(v.basis?.detail).toBe("12 / 39건");
    // 큰 수가 이미 건수 진척이므로 아래 줄에 또 쓰면 같은 %가 두 번 나온다.
    expect(v.count).toBeNull();
    expect(v.note).toBe("클립이 없어 시간 대신 건수로 집계합니다");
  });

  it("시간을 재면 시간이 큰 수이고 건수는 아래 줄로 내려간다", () => {
    const v = progressView(
      kpi({
        done_count: 92,
        total_count: 283,
        progress_pct: 32.5,
        done_hours: 3.6,
        hours_progress: { pct: 12.4, basis: "target", scale_hours: 29 },
      }),
    );
    expect(v.headline).toBe("12.4%");
    expect(v.basis?.label).toBe("계약 목표");
    expect(v.count).toEqual({ pct: "32.5%", done: 92, total: 283 });
  });

  it("시간 못 잰 0은 줄을 만들지 않는다", () => {
    const v = progressView(kpi({ total_count: 10, progress_pct: 0 }));
    expect(v.hoursUnmeasurable).toBeNull();
  });

  it("시간 못 잰 건수는 있을 때만 싣는다", () => {
    const v = progressView(kpi({ total_count: 10, progress_pct: 0, hours_unmeasurable_count: 11 }));
    expect(v.hoursUnmeasurable).toBe(11);
  });
});

describe("정의 문구", () => {
  it("뜻을 먼저 쓰고 계산식은 괄호로 뒤에 둔다 — 통계 용어와 내부 말을 쓰지 않는다", () => {
    const defines = [COUNT_BASIS_DEFINE, ...Object.values(HOURS_BASIS_DEFINE), WORK_POPULATION_DEFINE];
    for (const d of defines) expect(d).not.toMatch(/분모|영상 단위 표식|벽시계/);
    expect(COUNT_BASIS_DEFINE.startsWith("전체 작업 중 승인된 작업의 비율입니다.")).toBe(true);
    expect(COUNT_BASIS_DEFINE.endsWith("(승인 ÷ 전체 작업)")).toBe(true);
  });
});
