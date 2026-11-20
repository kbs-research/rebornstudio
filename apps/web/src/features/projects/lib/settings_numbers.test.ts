/**
 * 설정 숫자 입력란 테스트. 화면의 규격이 계약과 같은지,
 * 저장 전 검사가 서버와 같은 기준인지 확인한다.
 *
 * `tsc`는 둘 다 잡지 못한다. 범위는 수라서 타입이 좁혀 주지 않고,
 * 정수 여부는 타입(`number`)에 없다.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  NUMBER_SPECS,
  REQUIRED_TEXT,
  boundsText,
  checkNumber,
  type NumberField,
  type NumberSpec,
} from "./settings_numbers";

type Prop = { type?: string; minimum?: number; maximum?: number; anyOf?: Prop[] };
const openapi = JSON.parse(
  readFileSync(resolve(fileURLToPath(new URL(".", import.meta.url)), "../../../../../../docs/api/openapi.json"), "utf-8"),
) as { components: { schemas: Record<string, { properties: Record<string, Prop> }> } };
const schemas = openapi.components.schemas;

/** 폼 필드 id별 계약의 (스키마, 속성). */
const CONTRACT: Record<NumberField, [string, string]> = {
  target_hours: ["ProjectUpdateRequest", "target_hours"],
};

/** `Optional[int]`은 `anyOf: [{type: integer, …}, {type: null}]`로 나온다. 수 타입 쪽을 고른다. */
function numericBranch(p: Prop): Prop {
  return p.anyOf?.find((b) => b.type === "integer" || b.type === "number") ?? p;
}

describe("화면 규격 = 계약", () => {
  it.each(Object.keys(NUMBER_SPECS) as NumberField[])("%s", (field) => {
    const [schema, prop] = CONTRACT[field];
    const p = numericBranch(schemas[schema].properties[prop]);
    const spec = NUMBER_SPECS[field];
    expect(spec.min).toBe(p.minimum);
    expect(spec.max).toBe(p.maximum ?? null);
    // 정수 여부도 계약에서 가져온다. 없으면 5.5가 입력란 검사를 통과하고 서버에서 거절된다.
    expect(spec.int).toBe(p.type === "integer");
  });
});

describe("저장 전 검사", () => {
  /* 지금 설정 화면에는 정수·필수 숫자 입력란이 없다. 그런 입력란이 생겨도 같은 검사가 적용되도록 규격 하나로 확인한다. */
  const limit: NumberSpec = { min: 1, max: 50, int: true, clearable: false };

  it("범위 안의 소수는 정수 칸에서 걸리고 문구가 «정수»를 말한다", () => {
    expect(checkNumber("5.5", limit)).toBe("1~50 사이의 정수로 입력해주세요.");
  });

  it("범위 밖도 같은 문장이다 — 서버 422가 둘을 같은 칸으로 짚는다", () => {
    expect(checkNumber("0", limit)).toBe(boundsText(limit));
    expect(checkNumber("51", limit)).toBe(boundsText(limit));
  });

  it("가장자리는 통과한다", () => {
    expect(checkNumber("1", limit)).toBeNull();
    expect(checkNumber("50", limit)).toBeNull();
  });

  it("비울 수 없는 칸을 비우면 막는다 — 서버는 그 null을 422로 돌려보낸다", () => {
    expect(checkNumber("", limit)).toBe(REQUIRED_TEXT);
    expect(checkNumber("  ", limit)).toBe(REQUIRED_TEXT);
  });

  it("비울 수 있는 칸은 비워도 된다 — 목표 시간은 «목표 없음»이다", () => {
    expect(checkNumber("", NUMBER_SPECS.target_hours)).toBeNull();
    expect(checkNumber("12.5", NUMBER_SPECS.target_hours)).toBeNull();
    expect(checkNumber("-1", NUMBER_SPECS.target_hours)).toBe("0 이상으로 입력해주세요.");
  });
});

/**
 * 비울 수 있는지(`clearable`)는 서버가 돌려주는 값이 `null`일 수 있는지와 같다.
 * 수정 요청(`ProjectUpdateRequest`)은 모든 필드가 선택이라 기준이 되지 못해, 조회 DTO를 본다.
 * 서버 쪽 짝은 `apps/api-gateway/tests/org/test_project_update.py`의 컬럼 nullable 대조다.
 */
const READ_SCHEMA: Record<string, string> = { ProjectUpdateRequest: "ProjectDTO" };

describe("비울 수 있는지 = 계약의 null 허용", () => {
  it.each(Object.keys(NUMBER_SPECS) as NumberField[])("%s", (field) => {
    const [schema, prop] = CONTRACT[field];
    const p = schemas[READ_SCHEMA[schema] ?? schema].properties[prop];
    const nullable = (p.anyOf ?? []).some((b) => b.type === "null");
    expect(NUMBER_SPECS[field].clearable).toBe(nullable);
  });
});
