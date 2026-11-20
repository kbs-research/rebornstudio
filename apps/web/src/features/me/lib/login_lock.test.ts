/** 로그인 잠금 줄의 보조 문구를 검사한다. */
import { describe, expect, it } from "vitest";

import { lockDetail } from "./login_lock";

describe("로그인 잠금 보조 문구", () => {
  it("경로·실패 횟수·잠금 시간을 한 줄로 쓴다", () => {
    expect(lockDetail({ via: "login", failures: 50, lock_seconds: 900 })).toBe("로그인 실패 50회 · 15분 잠금");
    expect(lockDetail({ via: "password_change", failures: 50, lock_seconds: 900 })).toBe("비밀번호 변경 실패 50회 · 15분 잠금");
  });

  it("사전에 없는 경로는 코드를 쓰지 않고 횟수만 쓴다", () => {
    expect(lockDetail({ via: "sso", failures: 50, lock_seconds: 900 })).toBe("실패 50회 · 15분 잠금");
  });

  it("값이 없으면 지어내지 않는다", () => {
    expect(lockDetail({ lock_seconds: 900 })).toBe("15분 잠금");
    expect(lockDetail({})).toBeUndefined();
  });
});
