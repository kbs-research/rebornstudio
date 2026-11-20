/** 전역 실패 토스트에서 셸이 따로 알리는 실패를 빼는 규칙을 본다. */
import { describe, expect, it } from "vitest";

import { shellHandles } from "./queryClient";

describe("shellHandles — 셸이 따로 알리는 실패", () => {
  it("만료(401)와 사용할 수 없는 계정(403 account_not_active)은 토스트로 다시 말하지 않는다", () => {
    expect(shellHandles({ status: 401, detail: "invalid_token" })).toBe(true);
    expect(shellHandles({ status: 403, detail: "account_not_active" })).toBe(true);
  });
  it("동의 전 요청(403 privacy_consent_required)은 동의 화면이 알리므로 토스트로 말하지 않는다", () => {
    expect(shellHandles({ status: 403, detail: "privacy_consent_required" })).toBe(true);
    // 동의 화면·최초 설정 요청의 422는 같은 상태 코드라도 폼이 직접 알린다.
    expect(shellHandles({ status: 422, detail: "privacy_consent_required" })).toBe(false);
  });
  it("다른 403과 일시 오류는 토스트로 알린다", () => {
    expect(shellHandles({ status: 403, detail: "not_a_member" })).toBe(false);
    expect(shellHandles({ status: 503, detail: "service_unavailable" })).toBe(false);
    expect(shellHandles(undefined)).toBe(false);
  });
});
