import { describe, expect, it } from "vitest";

import { failedRowAction, retryOffered } from "./row_action";

describe("failedRowAction", () => {
  it("서버가 건너뛰기를 연 줄은 재시도 대신 건너뛰기를 둔다", () => {
    expect(failedRowAction({ skippable: true }, { retry: true, skip: true })).toBe("skip");
  });

  it("건너뛰기를 열지 않은 줄은 지금처럼 재시도를 둔다", () => {
    expect(failedRowAction({ skippable: false }, { retry: true, skip: true })).toBe("retry");
    expect(failedRowAction({}, { retry: true, skip: true })).toBe("retry");
  });

  it("건너뛰기를 부를 수 없으면(관리자가 아님) 열린 줄이어도 건너뛰기를 두지 않는다", () => {
    expect(failedRowAction({ skippable: true }, { retry: false, skip: false })).toBeNull();
  });
});

describe("retryOffered", () => {
  it("장면 요약 줄은 서버가 연 줄(`retryable`)에만 재시도를 둔다", () => {
    expect(retryOffered({ stage: "summarize", retryable: true }, true)).toBe(true);
    expect(retryOffered({ stage: "summarize", retryable: false }, true)).toBe(false);
    expect(retryOffered({ stage: "summarize" }, true)).toBe(false);
  });

  it("다른 단계는 화면의 복구 경로로 정한다", () => {
    expect(retryOffered({ stage: "transcode" }, true)).toBe(true);
    expect(retryOffered({ stage: "summarize", retryable: true }, false)).toBe(false);
  });
});
