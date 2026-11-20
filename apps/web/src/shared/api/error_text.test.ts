/**
 * 422 문구 테스트. 가리킬 입력란이 있는지에 따라 문구가 달라진다.
 *
 * 필드 경로가 `body` 하나뿐인 422는 입력값 문구 대신 다른 문구를 쓴다.
 * 입력란이 없는 화면에서 "입력값이 올바르지 않습니다"라고 안내하면 누른 사람은 고칠 곳을 찾을 수 없다.
 */
import { describe, expect, it } from "vitest";

import { errorText } from "./error_text";

describe("errorText — 422", () => {
  it("칸을 짚을 수 있으면 입력값을 고치라고 말한다", () => {
    expect(errorText("validation_error").message).toBe("입력값이 올바르지 않습니다");
  });

  it("짚을 칸이 없으면 사람 탓으로 돌리지 않는다", () => {
    const t = errorText("validation_error_no_field");
    expect(t.message).toBe("요청이 서버 규격과 맞지 않습니다");
    expect(t.message).not.toContain("입력값");
  });

  it("모르는 코드는 원문을 화면에 내지 않는다", () => {
    expect(errorText("some_new_code_2026").message).toBe("처리하지 못했습니다");
  });

  it("서버가 보낸 한국어 문장은 그대로 쓴다", () => {
    expect(errorText("비밀번호는 12자 이상이어야 합니다").message).toBe("비밀번호는 12자 이상이어야 합니다");
  });
});
