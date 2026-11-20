/**
 * 고를 연결이 없을 때의 안내 문구 테스트. 무엇이 없는지와 어디서 등록하는지를 두 줄로 나눠 쓰는지 검사한다.
 * 자격증명 연결 화면을 열 수 없는 사람에게는 누가 등록하는지를 쓴다.
 */
import { describe, expect, it } from "vitest";

import { noConnectionText } from "./NoConnectionNotice";

describe("noConnectionText", () => {
  it("없는 연결을 결과로, 등록할 곳을 안내로 나눈다", () => {
    const t = noConnectionText("storage", true);
    expect(t.title).toBe("고를 수 있는 NCP 저장소 연결이 없습니다");
    expect(t.body).toContain("자격증명 연결");
    expect(t.body).not.toContain("최고관리자");
  });

  it("자격증명 연결을 열 수 없는 사람에게는 최고관리자가 등록한다고 쓴다", () => {
    const t = noConnectionText("analysis", false);
    expect(t.title).toContain("분석 연결");
    expect(t.body).toContain("최고관리자가 자격증명 연결에서");
  });

  it("둘 다 없으면 두 연결을 함께 말한다", () => {
    expect(noConnectionText("both", true).title).toContain("NCP 저장소 연결과 분석 연결");
  });
});
