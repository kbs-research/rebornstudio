/** 분석 요청 본문이 계약에 있는 필드만 싣는지 확인한다. 서버는 모르는 필드를 422로 거절한다. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { dispatchBody } from "./dispatch_body";

const openapi = JSON.parse(
  readFileSync(resolve(fileURLToPath(new URL(".", import.meta.url)), "../../../../../../docs/api/openapi.json"), "utf-8"),
) as { components: { schemas: Record<string, { properties: Record<string, unknown> }> } };

describe("dispatchBody", () => {
  it("대상 영상만 싣는다", () => {
    expect(dispatchBody(["v1", "v2"])).toEqual({ video_ids: ["v1", "v2"] });
  });

  it("클립 길이를 싣지 않는다", () => {
    expect(dispatchBody(["v1"])).not.toHaveProperty("scene_options");
  });

  it("계약이 받는 필드만 싣는다", () => {
    const accepted = new Set(Object.keys(openapi.components.schemas.MaiuDispatchRequest.properties));
    for (const k of Object.keys(dispatchBody(["v1"]))) expect(accepted.has(k), k).toBe(true);
  });
});
