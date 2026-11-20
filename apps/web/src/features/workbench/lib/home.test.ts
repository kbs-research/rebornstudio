/** 라벨링 워크벤치의 돌아갈 곳(`labelWorkbenchHome`)·작업을 놓은 뒤 안내(`afterReleaseHint`)·읽기 전용 사유(`readOnlyReason`) 검사. */
import { describe, expect, it } from "vitest";

import { afterReleaseHint, labelWorkbenchHome, readOnlyReason } from "./home";

describe("afterReleaseHint", () => {
  it("돌아갈 화면만 쓴다. 신고로 놓은 작업을 냈다고 쓰지 않는다", () => {
    const hint = afterReleaseHint("내 작업");
    expect(hint).toBe("내 작업으로 돌아갑니다.");
    expect(hint).not.toContain("냈");
  });

  it("운영 역할의 목적지 이름에도 조사를 맞춘다", () => {
    expect(afterReleaseHint("라벨링")).toBe("라벨링으로 돌아갑니다.");
  });
});

describe("labelWorkbenchHome", () => {
  it("운영 역할은 작업 단계의 라벨링 화면으로 간다", () => {
    expect(labelWorkbenchHome(true, "default")).toEqual({ to: "/p/default/pipeline?stage=label", label: "라벨링" });
  });

  it("작업자는 내 작업으로 간다", () => {
    expect(labelWorkbenchHome(false, "default")).toEqual({ to: "/my/work", label: "내 작업" });
  });

  it("프로젝트 주소를 모르면 운영 역할도 내 작업으로 간다", () => {
    expect(labelWorkbenchHome(true, undefined)).toEqual({ to: "/my/work", label: "내 작업" });
  });
});

describe("readOnlyReason", () => {
  it("제출 문장은 보는 사람에 따라 주어가 다르다", () => {
    expect(readOnlyReason({ status: "submitted" }, false)).toMatch(/^제출한 작업/);
    expect(readOnlyReason({ status: "submitted" }, true)).toMatch(/^제출된 작업/);
  });

  it("종료된 작업을 아무도 맡지 않은 작업으로 말하지 않는다", () => {
    expect(readOnlyReason({ status: "cancelled" }, true)).toBe("종료된 작업은 고칠 수 없습니다.");
  });

  it("보유 여부는 보유자로 본다", () => {
    expect(readOnlyReason({ status: "claimed", claimed_by: "u1" }, true)).toMatch(/^다른 사람이 맡은/);
    expect(readOnlyReason({ status: "claimed", claimed_by: null }, true)).toMatch(/^아직 아무도/);
  });
});
