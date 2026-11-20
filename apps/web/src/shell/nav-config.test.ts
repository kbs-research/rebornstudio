/**
 * 사이드바 항목의 노출·이름·활성 판정을 검증한다.
 *
 * 사이드바는 라우터 가드와 같은 표를 읽어야 열 수 없는 화면을 가리키지 않는다.
 * 누른 메뉴와 열린 화면의 이름이 다르거나 활성 항목이 없어도 `tsc`는 잡아내지 못한다.
 */
import { describe, expect, it } from "vitest";

import { type ProjectRole } from "@/lib/roles";

import {
  isPlatformPath,
  platformItemActive,
  SURFACES,
  surfaceLink,
  surfaceVisible,
} from "./nav-config";

/** 역할이 보는 프로젝트 표면의 id 목록을 돌려준다. */
const visibleIds = (role: ProjectRole | null, isSuper = false) =>
  SURFACES.filter((s) => surfaceVisible(s, role, isSuper)).map((s) => s.id);
const stagesItem = SURFACES.find((s) => s.id === "stages")!;
/** 표면 링크를 만들 맥락을 만든다. */
const ctx = (role: ProjectRole | null, isSuper = false) => ({ slug: "default", role, isSuper });

describe("표면 노출", () => {
  it("역할마다 보이는 항목 수가 머리 주석과 같다", () => {
    expect(visibleIds("project_admin")).toHaveLength(7);
    expect(visibleIds("reviewer")).toEqual(["overview"]);
    expect(visibleIds("annotator")).toEqual(["overview"]);
  });

  it("관리자 메뉴는 개요 다음에 작업 단계가 온다", () => {
    expect(visibleIds("project_admin")).toEqual([
      "overview", "stages", "content", "qc", "people", "insights", "settings",
    ]);
  });

  it("최고관리자는 전부, 멤버가 아니면 아무것도 안 본다", () => {
    expect(visibleIds(null, true)).toHaveLength(SURFACES.length);
    expect(visibleIds(null)).toEqual([]);
  });
});

describe("표면 링크", () => {
  it("이름과 목적지는 역할과 상관없이 표면 정의 그대로다", () => {
    const insights = SURFACES.find((s) => s.id === "insights")!;
    expect(surfaceLink(insights, ctx("reviewer"))).toMatchObject({ label: "통계", to: "/p/default/insights" });
    expect(surfaceLink(stagesItem, ctx("project_admin"))).toMatchObject({ label: "작업 단계", to: "/p/default/pipeline" });
    expect(surfaceLink(stagesItem, ctx(null, true)).label).toBe("작업 단계");
  });
});

describe("플랫폼 메뉴", () => {
  it("플랫폼 화면만 플랫폼 경로로 본다", () => {
    expect(isPlatformPath("/admin")).toBe(true);
    expect(isPlatformPath("/admin/users")).toBe(true);
    expect(isPlatformPath("/administrator")).toBe(false);
    expect(isPlatformPath("/p/default/admin/dashboard")).toBe(false);
  });

  it("개요는 하위 화면에서 켜지지 않는다", () => {
    expect(platformItemActive("/admin", "/admin")).toBe(true);
    expect(platformItemActive("/admin", "/admin/users")).toBe(false);
    expect(platformItemActive("/admin/users", "/admin/users")).toBe(true);
  });
});
