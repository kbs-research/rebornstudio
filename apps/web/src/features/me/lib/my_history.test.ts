/** 내 라벨링 기록의 이름·상태 거르기·행 수 문구·판정 표시 규칙을 검사한다. */
import { describe, expect, it } from "vitest";

import {
  MY_STATE,
  MY_STATE_FILTER,
  historyCountLabel,
  historyTargetLabel,
  verdictAddsInfo,
  type MyWorkHistoryRow,
} from "./my_history";

/** 테스트용 행을 만든다. 필요한 필드만 채운다. */
function row(over: Partial<MyWorkHistoryRow> = {}): MyWorkHistoryRow {
  return {
    work_item_id: "43d9f604-0000-0000-0000-000000000000",
    video_id: "v",
    project_id: "p",
    status: "submitted",
    rejection_count: 0,
    payload: {},
    created_at: "2026-09-01T00:00:00Z",
    history_at: "2026-09-01T00:00:00Z",
    my_state: "submitted",
    my_submit_count: 1,
    video_title: "뉴스 브리핑 샘플 2026.08.10 (시범 방송)",
    clip_start_seconds: 72,
    clip_end_seconds: 100,
    ...over,
  } as MyWorkHistoryRow;
}

describe("historyTargetLabel", () => {
  it("영상 제목과 클립 구간으로 부른다", () => {
    expect(historyTargetLabel(row())).toBe("뉴스 브리핑 샘플 2026.08.10 (시범 방송) · 1:12–1:40");
  });
  it("제목도 구간도 없으면 작업 ID 앞자리로 부른다", () => {
    expect(
      historyTargetLabel(row({ video_title: null, clip_start_seconds: null, clip_end_seconds: null })),
    ).toBe("작업 43d9f604");
  });
});

describe("historyCountLabel", () => {
  it("뒤가 더 있으면 전체라고 하지 않는다", () => {
    expect(historyCountLabel(50, true, false)).toBe("불러온 50건 · 더 있음");
  });
  it("조건이 걸려 있으면 전체를 붙이지 않는다", () => {
    expect(historyCountLabel(3, false, true)).toBe("3건");
    expect(historyCountLabel(1234, false, false)).toBe("전체 1,234건");
  });
});

describe("MY_STATE_FILTER", () => {
  it("영문 상태값을 쓰지 않고 옛 상태(rejected) 말고는 전부 고를 수 있다", () => {
    const values = MY_STATE_FILTER.map((o) => o.value).sort();
    const all = (Object.keys(MY_STATE) as (keyof typeof MY_STATE)[]).filter((s) => s !== "rejected").sort();
    expect(values).toEqual(all);
    for (const o of MY_STATE_FILTER) expect(o.label).toMatch(/[가-힣]/);
  });
});

describe("verdictAddsInfo", () => {
  it("판정 전이면 쓰지 않는다(상태 칸의 검수 대기가 말한다)", () => {
    expect(verdictAddsInfo(row({ my_state: "submitted", my_verdict: null }))).toBe(false);
  });
  it("상태와 판정이 모두 승인이면 쓰지 않는다", () => {
    expect(verdictAddsInfo(row({ my_state: "approved", my_verdict: "approve" }))).toBe(false);
  });
  it("수정 승인은 상태 칸이 말하지 않으므로 쓴다", () => {
    expect(verdictAddsInfo(row({ my_state: "approved", my_verdict: "modify_and_approve" }))).toBe(true);
  });
  it("넘어가거나 돌아간 작업의 판정은 쓴다", () => {
    expect(verdictAddsInfo(row({ my_state: "returned", my_verdict: "reject" }))).toBe(true);
    expect(verdictAddsInfo(row({ my_state: "handed_over", my_verdict: "approve" }))).toBe(true);
  });
});
