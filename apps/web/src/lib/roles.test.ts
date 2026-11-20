/**
 * 역할 판정 테스트. 라우터 가드와 화면이 같은 목록을 읽는지 확인한다.
 *
 * 목록에서 한 줄을 빠뜨려도 타입은 `ProjectRole[]` 그대로라 `tsc`는 이 문제를 잡아내지 못한다.
 * 그래서 잘못된 가드는 실제 화면에서 누군가 들어가지 못하고 나서야 드러난다.
 * 같은 내용을 여는 화면(완료 목록·완료 상세)이 같은 가드를 쓰는지도 이 파일에서 확인한다.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  allowsRole,
  deniedCopy,
  hasProjectRole,
  homeLanding,
  landingFor,
  landingStage,
  moveLabel,
  NON_ANNOTATOR_PROJECT_ROLES,
  PIPELINE_STAGES,
  PROJECT_LIST_LANDING,
  ROUTE_ACCESS,
  STAGE_ROLES,
  visibleStages,
  WORK_RESULT_VIEWER_ROLES,
  type ProjectRole,
} from "./roles";
import { globalRoleLabel, projectRoleLabel } from "./role_label";
import type { ProjectDTO, UserDTO } from "@/shared/api/client";

/** 프로젝트 역할 전체 목록. 서버 CHECK 제약(`models/org.py`)과 같은 세 개다. */
const ALL_PROJECT_ROLES: ProjectRole[] = ["project_admin", "reviewer", "annotator"];

/** 그 프로젝트에서 `role`을 가진 사람의 프로젝트 DTO를 만든다. */
const member = (role: ProjectRole) => ({ my_role: role }) as ProjectDTO;
/** 플랫폼 역할만 가진 사용자 DTO를 만든다. */
const user = (globalRole: string | null) => ({ global_role: globalRole }) as UserDTO;

describe("NON_ANNOTATOR_PROJECT_ROLES", () => {
  it("라벨러만 빠진다", () => {
    const blocked = ALL_PROJECT_ROLES.filter((r) => !NON_ANNOTATOR_PROJECT_ROLES.includes(r));
    expect(blocked).toEqual(["annotator"]);
  });

  it("파이프라인 라우트를 라벨러는 못 열고 관리자와 검수자는 연다", () => {
    expect(hasProjectRole(user("user"), member("annotator"), NON_ANNOTATOR_PROJECT_ROLES)).toBe(false);
    for (const role of ["reviewer", "project_admin"] as ProjectRole[]) {
      expect(hasProjectRole(user("user"), member(role), NON_ANNOTATOR_PROJECT_ROLES)).toBe(true);
    }
  });

  it("최고관리자는 멤버가 아니어도 통과한다 — 서버 가드와 같은 규칙", () => {
    expect(hasProjectRole(user("super_admin"), undefined, NON_ANNOTATOR_PROJECT_ROLES)).toBe(true);
  });

  it("멤버가 아니면 막힌다 — 역할이 없는 것과 라벨러인 것을 섞지 않는다", () => {
    expect(hasProjectRole(user("user"), undefined, NON_ANNOTATOR_PROJECT_ROLES)).toBe(false);
  });
});

/* 산출물 열람 목록은 서버 `authz._WORK_RESULT_VIEWER_ROLES`와 같은 관리자·검수자다. */
describe("WORK_RESULT_VIEWER_ROLES", () => {
  it("서버 목록과 같다", () => {
    expect([...WORK_RESULT_VIEWER_ROLES].sort()).toEqual(["project_admin", "reviewer"]);
  });

  it("라벨러는 산출물도 파이프라인도 막힌다", () => {
    const m = member("annotator");
    expect(hasProjectRole(user("user"), m, WORK_RESULT_VIEWER_ROLES)).toBe(false);
    expect(hasProjectRole(user("user"), m, NON_ANNOTATOR_PROJECT_ROLES)).toBe(false);
  });

  it("관리자와 검수자는 산출물을 본다", () => {
    for (const role of ["reviewer", "project_admin"] as ProjectRole[]) {
      expect(hasProjectRole(user("user"), member(role), WORK_RESULT_VIEWER_ROLES)).toBe(true);
    }
  });

  it("두 목록은 같은 역할을 담는다", () => {
    expect([...NON_ANNOTATOR_PROJECT_ROLES].sort()).toEqual([...WORK_RESULT_VIEWER_ROLES].sort());
  });
});

/**
 * 라우터 소스. 가드가 표의 키만 쓰는지 확인한다.
 * 라우트 표는 `app/router.tsx`와 feature마다의 `routes.tsx`로 나뉘므로 두 곳을 이어 읽는다.
 */
const SRC_DIR = resolve(fileURLToPath(new URL(".", import.meta.url)), "..");
const ROUTE_FILES = [
  resolve(SRC_DIR, "app/router.tsx"),
  ...readdirSync(resolve(SRC_DIR, "features"))
    .map((d) => resolve(SRC_DIR, "features", d, "routes.tsx"))
    .filter((f) => existsSync(f)),
];
const ROUTER_SRC = ROUTE_FILES.map((f) => readFileSync(f, "utf-8")).join("\n");

describe("ROUTE_ACCESS와 라우터", () => {
  const used = [...ROUTER_SRC.matchAll(/<RoleGuard route="(\w+)"/g)].map((m) => m[1]);

  it("라우터의 가드는 전부 표의 키를 쓴다", () => {
    expect(used.length).toBeGreaterThan(0);
    for (const key of used) expect(Object.keys(ROUTE_ACCESS)).toContain(key);
  });

  it("역할 배열을 직접 받는 가드가 남아 있지 않다", () => {
    expect(ROUTER_SRC).not.toMatch(/<RoleGuard\s+require=/);
  });

  it("표의 키는 전부 라우터가 쓴다", () => {
    for (const key of Object.keys(ROUTE_ACCESS)) expect(used).toContain(key);
  });

  it("파이프라인과 산출물 키는 기존 목록을 그대로 쓴다", () => {
    expect(ROUTE_ACCESS.pipeline.roles).toBe(NON_ANNOTATOR_PROJECT_ROLES);
    expect(ROUTE_ACCESS.workResult.roles).toBe(WORK_RESULT_VIEWER_ROLES);
  });
});

describe("allowsRole", () => {
  it("최고관리자는 프로젝트 범위 라우트를 멤버가 아니어도 연다", () => {
    expect(allowsRole(ROUTE_ACCESS.settings, "super_admin", null)).toBe(true);
  });

  it("플랫폼 라우트는 프로젝트 역할로 열리지 않는다", () => {
    expect(allowsRole(ROUTE_ACCESS.platform, "user", "project_admin")).toBe(false);
    expect(allowsRole(ROUTE_ACCESS.projectNew, "user", "project_admin")).toBe(false);
    expect(allowsRole(ROUTE_ACCESS.projectNew, "super_admin", null)).toBe(true);
  });

  it("검수자는 콘텐츠와 통계를 열지 못한다", () => {
    expect(allowsRole(ROUTE_ACCESS.content, "user", "reviewer")).toBe(false);
    expect(allowsRole(ROUTE_ACCESS.insights, "user", "reviewer")).toBe(false);
  });

  it("역할이 없으면 프로젝트 범위 라우트는 막힌다", () => {
    expect(allowsRole(ROUTE_ACCESS.content, "user", null)).toBe(false);
  });
});

describe("deniedCopy", () => {
  it("플랫폼 화면은 누가 여는지와 누구에게 요청하는지를 말한다", () => {
    expect(deniedCopy(ROUTE_ACCESS.platform, globalRoleLabel)).toEqual({
      message: "최고관리자만 볼 수 있는 화면입니다.",
      hint: "권한이 필요하면 최고관리자에게 요청해주세요.",
    });
  });

  it("프로젝트 화면은 역할을 넓은 순서로 잇는다", () => {
    expect(deniedCopy(ROUTE_ACCESS.content, projectRoleLabel).message).toBe("관리자만 볼 수 있는 화면입니다.");
    expect(deniedCopy(ROUTE_ACCESS.content, projectRoleLabel).hint).toBe("권한이 필요하면 프로젝트 관리자에게 요청해주세요.");
  });

  it("프로젝트 추가는 만드는 화면이라 문구가 다르다", () => {
    expect(deniedCopy(ROUTE_ACCESS.projectNew, globalRoleLabel).message).toBe(
      "프로젝트는 최고관리자만 추가할 수 있습니다.",
    );
  });

  it("라벨러에게는 권한 요청 대신 일하는 곳을 말한다", () => {
    expect(deniedCopy(ROUTE_ACCESS.pipeline, projectRoleLabel)).toEqual({
      message: "라벨링 작업은 내 작업에서 합니다.",
      hint: null,
    });
  });

  it("어느 문구에도 내부 용어 전역 역할이 없다", () => {
    for (const access of Object.values(ROUTE_ACCESS)) {
      const labelOf = access.scope === "global" ? globalRoleLabel : projectRoleLabel;
      const { message, hint } = deniedCopy(access, labelOf);
      expect(`${message} ${hint ?? ""}`).not.toContain("전역 역할");
    }
  });
});

describe("역할의 첫 화면", () => {
  it("라벨러·검수자는 내 작업이다", () => {
    for (const role of ["annotator", "reviewer"]) {
      expect(landingFor(role, false)).toEqual({ to: "/my/work", label: "내 작업" });
    }
  });

  it("관리자·최고관리자와 역할이 없는 사람은 프로젝트 목록이다", () => {
    expect(landingFor("project_admin", false)).toBe(PROJECT_LIST_LANDING);
    expect(landingFor("annotator", true)).toBe(PROJECT_LIST_LANDING);
    expect(landingFor(null, false)).toBe(PROJECT_LIST_LANDING);
  });

  it("홈이 착지에 쓴 프로젝트를 알려 준다. 고른 것이 없거나 낡았으면 첫 프로젝트다", () => {
    const projects = [
      { project_id: "a", slug: "alpha", my_role: "reviewer" },
      { project_id: "b", slug: "beta", my_role: "annotator" },
    ] as ProjectDTO[];
    expect(homeLanding(user("user"), projects, null)).toMatchObject({ to: "/my/work", projectId: "a" });
    expect(homeLanding(user("user"), projects, "옛값")?.projectId).toBe("a");
    expect(homeLanding(user("user"), projects, "b")?.projectId).toBe("b");
  });

  it("한 곳이라도 관리하면 홈은 프로젝트 목록이다", () => {
    const projects = [
      { project_id: "a", slug: "alpha", my_role: "annotator" },
      { project_id: "b", slug: "beta", my_role: "project_admin" },
    ] as ProjectDTO[];
    expect(homeLanding(user("user"), projects, "a")).toBeNull();
    expect(homeLanding(user("super_admin"), [projects[0]], "a")).toBeNull();
  });

  it("이동 버튼 문구는 받침에 맞는 조사를 쓴다", () => {
    expect(moveLabel("내 작업")).toBe("내 작업으로 이동");
    expect(moveLabel("내 활동")).toBe("내 활동으로 이동");
    expect(moveLabel("완료 목록")).toBe("완료 목록으로 이동");
    expect(moveLabel("프로젝트 목록")).toBe("프로젝트 목록으로 이동");
    expect(moveLabel("알림 메일")).toBe("알림 메일로 이동");
  });
});

/*
  단계 표(`STAGE_ROLES`)는 작업 단계 화면(`features/pipeline/pages/pipeline.tsx`)이 `visibleStages`·`landingStage`로 읽는다.
  아래 기대값은 역할마다 보이는 단계다.
*/
describe("단계 표", () => {
  const cases: [ProjectRole, string[]][] = [
    ["project_admin", ["ingest", "label", "review", "done"]],
    ["reviewer", ["review", "done"]],
    ["annotator", []],
  ];

  it.each(cases)("%s가 보는 단계", (role, stages) => {
    expect(visibleStages(role, false)).toEqual(stages);
  });

  it("최고관리자는 전 단계를 본다", () => {
    expect(visibleStages(null, true)).toEqual([...PIPELINE_STAGES]);
  });

  it("단계 역할의 합집합은 파이프라인 라우트 역할과 같다", () => {
    const union = new Set(Object.values(STAGE_ROLES).flat());
    expect([...union].sort()).toEqual([...NON_ANNOTATOR_PROJECT_ROLES].sort());
  });

  it("주소에 단계가 없으면 역할의 주 단계에 떨어진다", () => {
    expect(landingStage("reviewer", visibleStages("reviewer", false))).toBe("review");
    expect(landingStage("project_admin", visibleStages("project_admin", false))).toBe("ingest");
    expect(landingStage("annotator", visibleStages("annotator", false))).toBeUndefined();
  });
});
