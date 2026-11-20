import { describe, expect, it } from "vitest";

import type { MetaImportPreview } from "../api";
import { metaApplyItems, metaSkippedRows, metaValueText } from "./meta_import";

/** 행 판정 다섯 가지를 하나씩 담은 미리 보기. */
const preview: MetaImportPreview = {
  sheet: "메타",
  columns: ["video_id", "broadcaster", "aired_at"],
  ignored_columns: [],
  batch_video_count: 5,
  matched_video_count: 3,
  counts: { changed: 1, unchanged: 1, invalid: 1, unmatched: 1, ambiguous: 1 },
  rows: [
    {
      row: 2, status: "changed", video_id: "v1", title: "a", matched_by: "video_id", errors: [],
      changes: [
        { field: "broadcaster", before: null, after: "샘플 방송" },
        { field: "aired_at", before: null, after: "2026-05-28T00:00:00+09:00" },
      ],
    },
    { row: 3, status: "unchanged", video_id: "v2", title: "b", changes: [], errors: [] },
    {
      row: 4, status: "invalid", video_id: "v3", title: "c", errors: [{ field: "genre", message: "x" }],
      changes: [{ field: "broadcaster", before: null, after: "예시 방송" }],
    },
    { row: 5, status: "unmatched", changes: [], errors: [{ message: "x" }] },
    { row: 6, status: "ambiguous", changes: [], errors: [{ message: "x" }] },
  ],
};

describe("metaApplyItems", () => {
  it("변경 행만 반영 요청에 넣는다. 오류 행에 바뀌는 값이 있어도 넣지 않는다", () => {
    expect(metaApplyItems(preview)).toEqual([
      { video_id: "v1", fields: { broadcaster: "샘플 방송", aired_at: "2026-05-28T00:00:00+09:00" } },
    ]);
  });
});

describe("metaSkippedRows", () => {
  it("오류·일치 없음·중복 일치를 반영하지 않는 행으로 센다", () => {
    expect(metaSkippedRows(preview)).toBe(3);
  });
});

describe("metaValueText", () => {
  it("방영일은 KST 날짜로, 빈 값은 없음으로 쓴다", () => {
    expect(metaValueText("aired_at", "2026-05-27T15:00:00+00:00")).toBe("2026. 5. 28.");
    expect(metaValueText("broadcaster", null)).toBe("없음");
    expect(metaValueText("broadcaster", "샘플 방송")).toBe("샘플 방송");
  });
});
