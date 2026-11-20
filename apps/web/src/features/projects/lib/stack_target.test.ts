/** 분석 연동 카드의 문구 규칙을 검사한다. */
import { describe, expect, it } from "vitest";

import {
  NO_STREAK, TRANSIENT_RETRY_MAX, TRANSIENT_RETRY_MS, advanceStreak, lockTitle, projectOptionLabel, transientRetryDelay,
} from "./stack_target";

describe("lockTitle", () => {
  it("지운 영상이 없으면 등록된 수만 쓴다", () => {
    expect(lockTitle(5, 0, "저장소 대상")).toBe("영상 5개가 등록되어 저장소 대상을 바꿀 수 없습니다");
  });

  it("지운 영상이 있으면 그 수를 분모로 밝힌다", () => {
    expect(lockTitle(6, 1, "분석 대상")).toBe("영상 6개(삭제 1개 포함)가 등록되어 분석 대상을 바꿀 수 없습니다");
  });

  it("큰 수는 자릿수를 끊는다", () => {
    expect(lockTitle(1250, 12, "저장소 대상")).toBe("영상 1,250개(삭제 12개 포함)가 등록되어 저장소 대상을 바꿀 수 없습니다");
  });
});

describe("일시 오류 자동 재조회", () => {
  it("일시 오류를 받으면 간격을 두고 다시 부른다", () => {
    const s = advanceStreak(NO_STREAK, true, 1000);
    expect(s.n).toBe(1);
    expect(transientRetryDelay(s)).toBe(TRANSIENT_RETRY_MS);
  });
  it("같은 응답을 두 번 봐도 한 번만 센다", () => {
    const s = advanceStreak(NO_STREAK, true, 1000);
    expect(advanceStreak(s, true, 1000)).toBe(s);
  });
  it("상한까지만 스스로 다시 부른다", () => {
    let s = NO_STREAK;
    const delays: (number | false)[] = [];
    for (let i = 1; i <= TRANSIENT_RETRY_MAX + 2; i++) {
      s = advanceStreak(s, true, i * 1000);
      delays.push(transientRetryDelay(s));
    }
    expect(delays.filter((d) => d !== false)).toHaveLength(TRANSIENT_RETRY_MAX);
    expect(delays.at(-1)).toBe(false);
  });
  it("오류가 풀리면 수를 비우고 다시 부르지 않는다", () => {
    let s = advanceStreak(NO_STREAK, true, 1000);
    s = advanceStreak(s, false, 2000);
    expect(s.n).toBe(0);
    expect(transientRetryDelay(s)).toBe(false);
  });
  it("응답이 없으면 아무것도 세지 않는다", () => {
    expect(advanceStreak(NO_STREAK, true, 0)).toBe(NO_STREAK);
    expect(transientRetryDelay(NO_STREAK)).toBe(false);
  });
});

describe("projectOptionLabel — 저장된 분석 프로젝트의 이름", () => {
  it("이름을 알면 이름과 id를 같이 쓴다", () => {
    expect(projectOptionLabel("project_example", "101")).toBe("project_example · id 101");
  });
  it("이름을 모르거나 id와 같으면 id만 쓴다", () => {
    expect(projectOptionLabel(null, "101")).toBe("id 101");
    expect(projectOptionLabel("101", "101")).toBe("id 101");
  });
});
