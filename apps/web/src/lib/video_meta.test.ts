/** 영상 메타 테스트. 방영일을 KST 달력일로 주고받는지와 필드 이름을 검증한다. */
import { describe, expect, it } from "vitest";

import { airedAtFromInput, airedAtToInput, airedDate, VIDEO_META_FIELDS, VIDEO_META_NAME } from "./video_meta";

describe("video_meta — 방영일은 KST 달력일", () => {
  it("입력 날짜는 KST 자정으로 보낸다", () => {
    expect(airedAtFromInput("2026-09-01")).toBe("2026-09-01T00:00:00+09:00");
  });

  it("없는 날·형식이 틀린 값은 보내지 않는다", () => {
    expect(airedAtFromInput("2026-02-30")).toBeNull();
    expect(airedAtFromInput("9/1")).toBeNull();
    expect(airedAtFromInput("")).toBeNull();
  });

  it("UTC로 저장된 시각을 KST 날짜로 되읽는다 — 15:00 UTC 이후는 다음 날이다", () => {
    expect(airedAtToInput("2026-08-31T15:00:00Z")).toBe("2026-09-01");
    expect(airedAtToInput("2026-08-31T14:59:00Z")).toBe("2026-08-31");
    expect(airedAtToInput(null)).toBe("");
    expect(airedAtToInput("잘못된 값")).toBe("");
  });

  it("보내고 되읽으면 같은 날이다", () => {
    const sent = airedAtFromInput("2026-12-31")!;
    expect(airedAtToInput(sent)).toBe("2026-12-31");
    expect(airedDate(sent)).toBe("2026. 12. 31.");
    expect(airedDate(undefined)).toBeNull();
  });

  it("넷 모두 화면 이름이 있다 — 키가 화면에 새지 않는다", () => {
    for (const k of VIDEO_META_FIELDS) expect(VIDEO_META_NAME[k]).toMatch(/[가-힣]/);
  });
});
