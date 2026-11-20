/**
 * 연동 안내 문구 테스트. 저장소가 없으면 등록이, 분석이 없으면 분석 요청만 막혔다고 안내하는지 검사한다.
 * 결과(굵게)와 풀리는 방법(작게)이 나뉘어 있는지도 확인한다.
 */
import { describe, expect, it } from "vitest";

import { connectionNoticeText } from "./ConnectionNotice";

describe("connectionNoticeText", () => {
  it("둘 다 있으면 안내하지 않는다", () => {
    expect(connectionNoticeText(true, true)).toBeNull();
  });

  it("저장소가 없으면 등록·재생·납품이 막혔다고 말한다", () => {
    const t = connectionNoticeText(false, true)!;
    expect(t.title).toContain("영상 등록");
    expect(t.body).toContain("자격증명 연결");
    expect(t.body).toContain("NCP 저장소 연결");
    expect(t.body).not.toContain("분석 연결");
  });

  it("분석만 없으면 등록은 된다고 말한다", () => {
    const t = connectionNoticeText(true, false)!;
    expect(t.title).toContain("분석 요청");
    expect(t.title).not.toContain("영상 등록");
    expect(t.body).toContain("영상 등록은 됩니다");
  });

  it("분석 대상을 저장하지 않았으면 등록과 분석 요청이 막혔다고 말하고 설정으로 보낸다", () => {
    const t = connectionNoticeText(true, true, true)!;
    expect(t.title).toContain("영상 등록과 분석 요청");
    expect(t.body).toContain("분석 연동");
    expect(t.settings).toBe(true);
  });

  it("저장소가 없으면 분석 대상보다 저장소를 먼저 안내한다", () => {
    expect(connectionNoticeText(false, true, true)!.settings).toBeUndefined();
  });

  it("저장소 대상을 저장하지 않았으면 영상 등록이 막혔다고 말하고 설정으로 보낸다", () => {
    const t = connectionNoticeText(true, true, false, true)!;
    expect(t.title).toContain("저장소 대상");
    expect(t.title).not.toContain("분석 요청");
    expect(t.settings).toBe(true);
  });

  it("저장소·분석 대상이 모두 없으면 두 대상을 함께 안내한다", () => {
    const t = connectionNoticeText(true, true, true, true)!;
    expect(t.title).toContain("저장소·분석 대상");
    expect(t.title).toContain("영상 등록과 분석 요청");
  });

  it("둘 다 없으면 두 연결을 모두 안내한다", () => {
    const t = connectionNoticeText(false, false)!;
    expect(t.title).toContain("저장소 연결과 분석 연결");
    expect(t.body).toContain("자격증명 연결");
  });
});
