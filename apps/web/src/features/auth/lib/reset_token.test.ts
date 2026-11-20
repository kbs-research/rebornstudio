/** 재설정 입력란이 링크 전체를 받아도 토큰만 보내는지 확인한다. */
import { describe, expect, it } from "vitest";

import { addressWithoutToken, resetTokenFrom, tokenFromLocation } from "./reset_token";

describe("resetTokenFrom", () => {
  it("링크 전체를 붙여 넣으면 토큰만 꺼낸다", () => {
    expect(resetTokenFrom("https://studio.example.com/password-reset?token=abc123")).toBe("abc123");
  });

  it("인코딩된 토큰을 풀어서 꺼낸다", () => {
    expect(resetTokenFrom("https://x.example/password-reset?token=a%2Bb%3D")).toBe("a+b=");
  });

  it("상대 주소와 앞뒤 공백도 읽는다", () => {
    expect(resetTokenFrom("  /password-reset?token=xyz\n")).toBe("xyz");
  });

  it("주소 조각에 실린 토큰도 꺼낸다", () => {
    expect(resetTokenFrom("https://studio.example.com/password-reset#token=abc123")).toBe("abc123");
  });

  it("토큰만 붙여 넣었으면 그대로 쓴다", () => {
    expect(resetTokenFrom(" abc123 ")).toBe("abc123");
  });

  it("토큰이 빈 링크는 받은 값을 그대로 둔다. 서버가 판정한다", () => {
    expect(resetTokenFrom("https://x.example/password-reset?token=")).toBe("https://x.example/password-reset?token=");
  });
});

/** 링크로 들어온 주소에서 토큰을 꺼내는지 확인한다. 조각을 먼저 보고 옛 쿼리 링크도 읽는다. */
describe("tokenFromLocation", () => {
  it("조각의 토큰을 꺼낸다", () => {
    expect(tokenFromLocation("", "#token=a%2Bb")).toBe("a+b");
  });

  it("조각이 없으면 쿼리의 토큰을 꺼낸다", () => {
    expect(tokenFromLocation("?token=old", "")).toBe("old");
  });

  it("둘 다 없으면 null이다", () => {
    expect(tokenFromLocation("", "")).toBeNull();
  });
});

/** 읽은 토큰을 지운 주소를 만드는지 확인한다. 주소창과 방문 기록에 토큰을 남기지 않는다. */
describe("addressWithoutToken", () => {
  it("쿼리와 조각의 토큰을 지우고 다른 값은 남긴다", () => {
    expect(addressWithoutToken("/password-reset", "?token=old&x=1", "#token=abc")).toBe("/password-reset?x=1");
  });

  it("토큰만 있으면 경로만 남긴다", () => {
    expect(addressWithoutToken("/password-reset", "", "#token=abc")).toBe("/password-reset");
  });
});
