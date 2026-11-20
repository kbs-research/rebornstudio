/**
 * 실패 응답을 `ApiError`로 바꾸는 규칙을 확인한다.
 *
 * 409 사용 중 거절은 막는 프로젝트 이름을 `detail` 옆 키로 싣는다. 이름을 옮기지 않으면
 * 화면이 먼저 바꿀 프로젝트를 안내하지 못하고, 다른 실패에 이름 필드가 생기면 빈 안내 줄이 그려진다.
 */
import { describe, expect, it } from "vitest";

import { inUseProjects, responseError } from "./api_error";

/** 본문과 상태 코드를 정한 응답을 만든다. */
function reply(status: number, body: unknown, statusText = "Conflict"): Response {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return new Response(text, { status, statusText });
}

describe("responseError", () => {
  it("사용 중 거절의 프로젝트 이름과 나머지 수를 옮긴다", async () => {
    const e = await responseError(reply(409, { detail: "connection_in_use", projects: ["가", "나"], projects_more: 3 }));
    expect(e).toEqual({ status: 409, detail: "connection_in_use", projects: ["가", "나"], projectsMore: 3 });
  });

  it("나머지가 없으면 수를 0으로 둔다", async () => {
    const e = await responseError(reply(409, { detail: "allowed_bucket_in_use", projects: ["가"], projects_more: 0 }));
    expect(e.projects).toEqual(["가"]);
    expect(e.projectsMore).toBe(0);
  });

  it("이름이 없는 실패에는 프로젝트 칸을 두지 않는다", async () => {
    const e = await responseError(reply(409, { detail: "name_exists" }));
    expect(e).toEqual({ status: 409, detail: "name_exists" });
    const empty = await responseError(reply(409, { detail: "connection_in_use", projects: [], projects_more: 0 }));
    expect(empty.projects).toBeUndefined();
  });

  it("본문이 JSON이 아니거나 null이면 상태 문구를 쓴다", async () => {
    expect(await responseError(reply(502, "<html>bad gateway</html>", "Bad Gateway"))).toEqual({ status: 502, detail: "Bad Gateway" });
    expect(await responseError(reply(500, "null", "Internal Server Error"))).toEqual({ status: 500, detail: "Internal Server Error" });
  });
});

describe("inUseProjects", () => {
  it("문자열이 아닌 이름과 빈 이름을 버린다", () => {
    expect(inUseProjects({ projects: ["가", 3, "", null, "나"], projectsMore: 1 })).toEqual({ names: ["가", "나"], more: 1 });
  });

  it("이름이 없거나 실패 객체가 아니면 null을 돌려준다", () => {
    expect(inUseProjects({ projects: [] })).toBeNull();
    expect(inUseProjects({ detail: "connection_in_use" })).toBeNull();
    expect(inUseProjects(null)).toBeNull();
    expect(inUseProjects("connection_in_use")).toBeNull();
  });

  it("음수이거나 숫자가 아닌 나머지 수는 0으로 둔다", () => {
    expect(inUseProjects({ projects: ["가"], projectsMore: -2 })).toEqual({ names: ["가"], more: 0 });
    expect(inUseProjects({ projects: ["가"], projectsMore: "4" })).toEqual({ names: ["가"], more: 0 });
  });
});
