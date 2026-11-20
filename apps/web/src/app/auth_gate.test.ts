/**
 * 보호 셸의 인증 가드가 셸을 그릴지, 어느 로그인 주소로 보낼지 정하는 판정을 본다.
 *
 * 세션이 만료되거나 토큰 없이 들어온 경우에는 돌아올 주소(`?next=`)를 붙인다.
 * 사용자가 이 탭이나 다른 탭에서 로그아웃한 경우에는 돌아갈 화면이 없으므로 `/login`만 쓴다.
 */
import { describe, expect, it } from "vitest";

import { authGate } from "./auth_gate";

const base = {
  authed: false,
  sessionExpired: false,
  signedOut: false,
  restoreUnavailable: false,
  pathname: "/projects",
  search: "",
};

describe("authGate", () => {
  it("로그인한 탭은 셸을 그린다", () => {
    expect(authGate({ ...base, authed: true })).toEqual({ kind: "shell" });
  });

  it("토큰 없이 들어오면 돌아올 주소를 붙여 로그인 화면으로 보낸다", () => {
    expect(authGate({ ...base, search: "?tab=1" })).toEqual({
      kind: "login",
      to: "/login?next=%2Fprojects%3Ftab%3D1",
    });
    expect(authGate({ ...base, pathname: "/" })).toEqual({ kind: "login", to: "/login" });
  });

  it("세션 만료 창을 띄운 동안에는 셸을 그대로 둔다", () => {
    expect(authGate({ ...base, sessionExpired: true })).toEqual({ kind: "shell" });
  });

  it("앱 시작 때 복구가 일시 오류면 연결 실패 패널을 그린다", () => {
    expect(authGate({ ...base, restoreUnavailable: true })).toEqual({ kind: "restore-failed" });
  });

  it("로그아웃으로 끝난 세션은 돌아올 주소 없이 로그인 화면으로 보낸다", () => {
    expect(authGate({ ...base, signedOut: true })).toEqual({ kind: "login", to: "/login" });
    expect(authGate({ ...base, signedOut: true, sessionExpired: true })).toEqual({
      kind: "login",
      to: "/login",
    });
    expect(authGate({ ...base, signedOut: true, restoreUnavailable: true })).toEqual({
      kind: "login",
      to: "/login",
    });
  });
});
