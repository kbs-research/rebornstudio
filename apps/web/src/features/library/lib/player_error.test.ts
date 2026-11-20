/** 재생기 오류 화면 검사. 다시 해도 같은 실패에 `다시 시도`를 주지 않는지 확인한다. */
import { describe, expect, it } from "vitest";

import { playerErrorView, UNKNOWN_REASON } from "./player_error";

describe("playerErrorView", () => {
  it("재생할 사본이 없는 409는 원인을 말하고 다시 시도를 주지 않는다", () => {
    expect(playerErrorView({ status: 409, detail: "invalid_source_uri" })).toEqual({
      title: "재생할 영상 없음",
      reason: "재생본이 없고 원본 파일도 연결되어 있지 않습니다.",
      hint: undefined,
      retry: false,
      code: undefined,
    });
  });

  it("원본을 받을 수 없는 사람의 재생본 없음은 준비 중으로 쓰고 다시 시도를 준다", () => {
    const v = playerErrorView({ status: 409, detail: "invalid_source_uri" }, { originalBlocked: true });
    expect(v.title).toBe("재생본 준비 중");
    expect(v.reason).toBe("재생본을 준비하는 중입니다.");
    expect(v.retry).toBe(true);
  });

  it("업로드가 끝나지 않은 409는 사전의 문구를 문장으로 쓴다", () => {
    const v = playerErrorView({ status: 409, detail: "video_not_uploaded" });
    expect(v.reason).toBe("아직 업로드가 끝나지 않았습니다.");
    expect(v.retry).toBe(false);
  });

  it("삭제된 영상은 영상 없음이다", () => {
    const v = playerErrorView({ status: 404, detail: "video_not_found" });
    expect(v.title).toBe("영상 없음");
    expect(v.retry).toBe(false);
  });

  it("권한이 없으면 볼 수 없는 영상이고 사전의 할 일을 붙인다", () => {
    const v = playerErrorView({ status: 403, detail: "not_a_member" });
    expect(v.title).toBe("볼 수 없는 영상");
    expect(v.hint).toBe("멤버는 프로젝트 관리자가 초대합니다.");
    expect(v.retry).toBe(false);
  });

  it("일시 오류와 응답 없음에는 다시 시도를 주고, 모르는 원인에만 코드를 붙인다", () => {
    expect(playerErrorView({ status: 503 })).toMatchObject({
      title: "영상 조회 실패", reason: UNKNOWN_REASON, retry: true, code: 503,
    });
    expect(playerErrorView(null)).toMatchObject({ retry: true, code: undefined });
  });
});
