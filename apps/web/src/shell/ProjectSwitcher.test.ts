/** 프로젝트 전환 뒤 갈 곳(`switchTarget`)을 검증한다. */
import { describe, expect, it } from "vitest";

import type { ProjectDTO } from "@/shared/api/client";

import { switchTarget } from "./ProjectSwitcher";

/** slug `b`에서 `my_role`을 가진 프로젝트 DTO를 만든다. */
const p = (my_role: ProjectDTO["my_role"]) => ({ slug: "b", my_role }) as ProjectDTO;

describe("switchTarget — 프로젝트를 바꾼 뒤 갈 곳", () => {
  it("개인 화면에서는 머문다", () => {
    expect(switchTarget("/my/work", p("annotator"), false)).toBeNull();
    expect(switchTarget("/my/activity", p("annotator"), false)).toBeNull();
  });
  it("프로젝트 표면이면 새 프로젝트의 같은 표면으로 간다", () => {
    expect(switchTarget("/p/a/insights", p("project_admin"), false)).toBe("/p/b/insights");
    expect(switchTarget("/p/a/pipeline", p("project_admin"), false)).toBe("/p/b/pipeline");
  });
  it("그 역할에게 안 보이는 표면이거나 하위 화면이면 개요로 간다", () => {
    expect(switchTarget("/p/a/settings", p("annotator"), false)).toBe("/p/b/overview");
    expect(switchTarget("/p/a/videos/123", p("project_admin"), false)).toBe("/p/b/overview");
    expect(switchTarget("/projects", p("annotator"), false)).toBe("/p/b/overview");
  });
  it("최고관리자는 모든 표면을 연다", () => {
    expect(switchTarget("/p/a/settings", p("project_admin"), true)).toBe("/p/b/settings");
  });
});
