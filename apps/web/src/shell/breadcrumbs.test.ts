/**
 * 브레드크럼이 지금 위치를 맞게 표시하는지 검증한다.
 *
 * 틀린 브레드크럼은 `tsc`도 빌드도 통과한다. 단계 이름이 빠져 `작업 단계`가 뜨거나,
 * 원문 세그먼트가 영문으로 찍히거나, 목록 링크가 엉뚱한 곳으로 가도 화면을 봐야 알 수 있다.
 * 그래서 라우터의 주소 전부를 풀어 보고 이름이 정해지지 않은 경로가 없는지 확인한다.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { type CrumbContext, resolveBreadcrumbs, UNKNOWN_LABEL } from "./breadcrumbs";

/** 경로의 브레드크럼 이름 목록을 돌려준다. */
const labels = (path: string, search = "", ctx: CrumbContext = {}) =>
  resolveBreadcrumbs(path, search, ctx).map((c) => c.label);
/** 경로의 끝 마디를 돌려준다. */
const last = (path: string, search = "", ctx: CrumbContext = {}) => {
  const crumbs = resolveBreadcrumbs(path, search, ctx);
  return crumbs[crumbs.length - 1];
};
const admin: CrumbContext = { projectName: "뉴스 프로젝트", role: "project_admin", globalRole: "user" };
/** 뉴스 프로젝트에서 `role`을 가진 사람의 맥락을 만든다. */
const as = (role: string): CrumbContext => ({ projectName: "뉴스 프로젝트", role, globalRole: "user" });

describe("작업 단계", () => {
  it("영상 등록과 완료도 단계 이름을 쓴다", () => {
    expect(last("/p/default/pipeline", "?stage=ingest", admin).label).toBe("영상 등록");
    expect(last("/p/default/pipeline", "?stage=done", admin).label).toBe("완료");
  });

  it("단계 없이 들어오면 역할이 떨어지는 단계 이름이다", () => {
    expect(last("/p/default/pipeline", "", as("reviewer")).label).toBe("검수");
    expect(last("/p/default/pipeline", "", admin).label).toBe("영상 등록");
  });

  it("역할에 없는 단계 주소는 파이프라인이 바꿔 여는 단계 이름이다", () => {
    expect(last("/p/default/pipeline", "?stage=label", as("reviewer")).label).toBe("검수");
  });

  it("볼 단계가 없는 라벨러에게는 단계 화면인 척하지 않는다", () => {
    expect(last("/p/default/pipeline", "?stage=label", as("annotator")).label).toBe("작업 단계");
  });

  it("역할을 아직 모르면 주소의 단계 이름을 그대로 쓴다", () => {
    expect(last("/p/default/pipeline", "?stage=review", { projectName: undefined }).label).toBe("검수");
    expect(last("/p/default/pipeline", "", {}).label).toBe("작업 단계");
  });
});

describe("프로젝트 마디", () => {
  it("목록 마디는 홈이 아니라 프로젝트 목록으로 간다", () => {
    expect(resolveBreadcrumbs("/p/default/overview", "", admin)[0]).toEqual({ label: "프로젝트", to: "/projects" });
  });

  it("이름을 받기 전에는 slug 대신 골격이다", () => {
    const crumbs = resolveBreadcrumbs("/p/default/overview", "", { projectName: undefined });
    expect(crumbs[1]).toEqual({ label: "", pending: true });
    expect(crumbs.map((c) => c.label)).not.toContain("default");
  });

  it("목록에 없는 프로젝트는 화면 이름 대신 프로젝트 없음이고, 있으면 개요로 링크한다", () => {
    expect(labels("/p/default/overview", "", { projectName: null })).toEqual(["프로젝트", "프로젝트 없음"]);
    expect(labels("/p/default/pipeline", "?stage=review", { projectName: null })).toEqual(["프로젝트", "프로젝트 없음"]);
    expect(resolveBreadcrumbs("/p/default/content", "", admin)[1]).toEqual({
      label: "뉴스 프로젝트",
      to: "/p/default/overview",
    });
  });
});

describe("개요와 통계", () => {
  it("역할과 상관없이 사이드바 항목과 같은 이름이다", () => {
    expect(last("/p/default/overview", "", admin).label).toBe("개요");
    expect(last("/p/default/overview", "", as("annotator")).label).toBe("개요");
    expect(last("/p/default/insights", "", as("reviewer")).label).toBe("통계");
  });
});

describe("없는 주소", () => {
  it("본문 제목과 같은 이름이다", () => {
    expect(UNKNOWN_LABEL).toBe("페이지를 찾을 수 없음");
  });
});

describe("상세 화면", () => {
  it("영상 상세는 콘텐츠를 부모로 두고 끝 마디에 영상 이름을 쓴다", () => {
    const crumbs = resolveBreadcrumbs("/p/default/videos/v1", "", { ...admin, pageTitle: "저녁 뉴스 샘플 0514" });
    expect(crumbs.slice(2)).toEqual([
      { label: "콘텐츠", to: "/p/default/content" },
      { label: "저녁 뉴스 샘플 0514" },
    ]);
  });

  it("콘텐츠를 못 여는 역할에게는 콘텐츠 마디가 없다", () => {
    expect(labels("/p/default/videos/v1", "", { ...as("reviewer"), pageTitle: "저녁 뉴스 샘플 0514" }))
      .toEqual(["프로젝트", "뉴스 프로젝트", "저녁 뉴스 샘플 0514"]);
  });

  it("제목을 받기 전에는 화면 종류 이름을 쓴다", () => {
    expect(last("/p/default/videos/v1", "", admin).label).toBe("영상 상세");
  });

  it("완료 상세는 완료 단계를 부모로 둔다", () => {
    const crumbs = resolveBreadcrumbs("/p/default/completed/w1", "", { ...as("reviewer"), pageTitle: "클립 12" });
    expect(crumbs.slice(2)).toEqual([
      { label: "완료", to: "/p/default/pipeline?stage=done" },
      { label: "클립 12" },
    ]);
  });

  it("상세가 아닌 화면은 올라온 제목으로 끝 마디를 바꾸지 않는다", () => {
    expect(last("/p/default/content", "", { ...admin, pageTitle: "다른 제목" }).label).toBe("콘텐츠");
  });
});

describe("최상위와 플랫폼", () => {
  it("프로젝트 추가는 목록을 부모로 둔다", () => {
    expect(resolveBreadcrumbs("/projects/new")).toEqual([
      { label: "프로젝트", to: "/projects" },
      { label: "프로젝트 추가" },
    ]);
  });

  it("플랫폼 화면은 플랫폼을 부모로 둔다", () => {
    expect(labels("/admin/users")).toEqual(["플랫폼", "사용자"]);
    expect(labels("/admin")).toEqual(["플랫폼"]);
  });

  it("모르는 경로는 원문 세그먼트를 쓰지 않는다", () => {
    expect(labels("/my/unknown")).toEqual([UNKNOWN_LABEL]);
    expect(last("/p/default/unknown", "", admin).label).toBe(UNKNOWN_LABEL);
    expect(last("/admin/unknown").label).toBe(UNKNOWN_LABEL);
  });
});

/**
 * 라우터 소스. 셸 안 주소를 전부 뽑아 풀어 본다.
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

describe("라우터의 모든 주소", () => {
  // 셸 안 주소는 앞에 `/`가 없다. 셸 밖(로그인 등)은 상단바가 없어 대상에서 뺀다.
  const paths = [...ROUTER_SRC.matchAll(/path: "([^"/*][^"]*)"/g)].map((m) => m[1]);
  const sample = (path: string) =>
    "/" + path.replace(":slug", "default").replace(/:(videoId|workItemId|id)/g, "x1");

  it("주소를 충분히 뽑았다", () => {
    expect(paths.length).toBeGreaterThan(20);
  });

  it.each(paths)("%s 는 모르는 경로 이름이나 원문 세그먼트를 쓰지 않는다", (path) => {
    const url = sample(path);
    const names = labels(url, "", admin);
    expect(names).not.toContain(UNKNOWN_LABEL);
    const raw = new Set(url.split("/").filter(Boolean));
    for (const name of names) expect(raw.has(name)).toBe(false);
  });
});
