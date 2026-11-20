/** 로그인 뒤 돌아갈 주소가 앱 안의 경로로만 이어지는지 확인한다. 바깥 주소로 가면 열린 리다이렉트가 된다. */
import { describe, expect, it } from "vitest";

import { safeNext } from "./safe_next";

describe("safeNext", () => {
  it("앱 안의 경로는 그대로 돌려준다", () => {
    expect(safeNext("/p/demo/work?tab=queue")).toBe("/p/demo/work?tab=queue");
  });

  it("값이 없거나 바깥 주소면 홈이다", () => {
    for (const raw of [null, "", "https://evil.example", "//evil.example", "evil.example"]) {
      expect(safeNext(raw)).toBe("/");
    }
  });

  it("역슬래시가 든 경로는 홈이다", () => {
    expect(safeNext("/\\evil.example")).toBe("/");
  });

  it("탭·줄바꿈 같은 제어 문자가 든 경로는 홈이다", () => {
    // 브라우저는 주소의 탭과 줄바꿈을 지우므로 `/\t/evil.example`은 `//evil.example`로 읽힌다.
    for (const raw of ["/\t/evil.example", "/\n/evil.example", "/\r/evil.example", "/\u0000/x", "/\u007f/x"]) {
      expect(safeNext(raw)).toBe("/");
    }
  });

  it("로그인 화면 자신으로는 보내지 않는다", () => {
    expect(safeNext("/login?next=/x")).toBe("/");
  });
});
