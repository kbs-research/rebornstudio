/**
 * 셸 본문 판정(`layoutBody`)과 주소 해석을 검증한다.
 *
 * 본문을 미루는 시점은 화면에 드러나지 않는다. 미루지 않으면 화면들이 지난번 프로젝트로 먼저 조회하고,
 * 너무 미루면 저장하지 않은 편집이 사라진다. 두 버그 모두 `tsc`가 잡아내지 못한다.
 */
import { describe, expect, it } from "vitest";

import { layoutBody, nextShownSlug, projectParamFromUrl, slugFromPath } from "./useSlugProject";

describe("layoutBody", () => {
  const base = { authFailed: false, slug: "alpha", shownSlug: null } as const;

  it("주소의 프로젝트로 맞추는 동안에는 본문을 미룬다", () => {
    expect(layoutBody({ ...base, state: "syncing" })).toBe("hold");
  });

  it("다른 프로젝트 화면에서 링크로 넘어와도 맞출 때까지 미룬다", () => {
    expect(layoutBody({ ...base, state: "syncing", shownSlug: "beta" })).toBe("hold");
  });

  it("이 slug의 본문을 이미 그렸으면 잠깐 어긋나도 내리지 않는다", () => {
    // 전환기로 다른 프로젝트를 고른 직후 이동이 막힌 경우다. 본문을 내리면 편집이 사라진다.
    expect(layoutBody({ ...base, state: "syncing", shownSlug: "alpha" })).toBe("page");
  });

  it("맞췄거나 주소에 프로젝트가 없으면 본문을 그린다", () => {
    expect(layoutBody({ ...base, state: "ok" })).toBe("page");
    expect(layoutBody({ ...base, slug: null, state: "none" })).toBe("page");
  });

  it("목록을 못 받았으면 미루지 않고 오류를 말한다", () => {
    expect(layoutBody({ ...base, state: "error" })).toBe("project-error");
  });

  it("없는 slug와 계정 조회 실패는 각자의 안내를 그린다", () => {
    expect(layoutBody({ ...base, state: "unknown" })).toBe("unknown-project");
    expect(layoutBody({ ...base, authFailed: true, state: "syncing" })).toBe("auth-error");
  });
});

describe("nextShownSlug", () => {
  it("맞춘 뒤에 그 slug를 기억한다", () => {
    expect(nextShownSlug(null, "alpha", "ok")).toBe("alpha");
    expect(nextShownSlug("alpha", "alpha", "syncing")).toBe("alpha");
  });

  it("프로젝트 밖으로 나가면 잊는다", () => {
    // 개인 화면에서 고른 프로젝트가 바뀐 뒤 같은 slug로 돌아오면 다시 미뤄야 한다.
    const left = nextShownSlug("alpha", null, "none");
    expect(left).toBeNull();
    expect(layoutBody({ authFailed: false, slug: "alpha", state: "syncing", shownSlug: left })).toBe("hold");
  });

  it("다른 slug로 옮겨 맞추는 중에는 앞의 slug를 들고 있어 미룬다", () => {
    const moving = nextShownSlug("alpha", "beta", "syncing");
    expect(layoutBody({ authFailed: false, slug: "beta", state: "syncing", shownSlug: moving })).toBe("hold");
  });
});

describe("주소 해석", () => {
  it("경로의 첫 `/p/{slug}`만 뽑는다", () => {
    expect(slugFromPath("/p/default/videos/1")).toBe("default");
    expect(slugFromPath("/my/work")).toBeNull();
  });

  it("경로에 slug가 있으면 `?project=`를 무시한다", () => {
    expect(projectParamFromUrl("/p/a/overview", "?project=b")).toBeNull();
    expect(projectParamFromUrl("/my/work", "?project=b")).toBe("b");
  });
});
