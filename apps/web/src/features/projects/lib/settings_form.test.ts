/** 설정 폼 표가 메뉴의 변경 표시, 저장 막대의 변경 목록, 저장 본문을 맞게 만드는지 확인한다. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  ALL_PAGES,
  EMPTY_FORM,
  FIELD_META,
  PAGE_GROUPS,
  changeList,
  changeRows,
  changeSummary,
  lineItemCount,
  pageFromParam,
  patchBody,
  type FormKey,
  type PageKey,
  type SettingsForm,
} from "./settings_form";
import { NUMBER_SPECS, type NumberField } from "./settings_numbers";

const BASE: SettingsForm = {
  ...EMPTY_FORM,
  name: "보도영상 1차",
  target_hours: "100",
};

const openapi = JSON.parse(
  readFileSync(resolve(fileURLToPath(new URL(".", import.meta.url)), "../../../../../../docs/api/openapi.json"), "utf-8"),
) as { components: { schemas: Record<string, { properties: Record<string, unknown> }> } };

/** 계약 스키마가 받는 속성 이름. */
const propsOf = (schema: string) => new Set(Object.keys(openapi.components.schemas[schema].properties));

/** 고정 기본값이라 서버가 422로 거절하는 처리 옵션. 저장 본문 어디에도 실리면 안 된다. */
const FIXED_FIELDS = [
  "default_assignment_ttl_hours", "caption_min_tokens", "reject_reasons", "scene_options",
  "sentence_timecode_enabled", "retain_original", "target_deident_pct",
  // 없어진 설정. 한 사람이 역할마다 한 건씩 맡아 동시 보유 한도가 없다.
  "default_max_concurrent_assignments", "review_max_concurrent",
];
const FIXED_QC_FIELDS = ["glossary", "profanity", "prompts", "prompt"];

describe("FIELD_META", () => {
  it("모든 필드가 저장하는 설정 페이지에 있다", () => {
    // 분석 연동과 위험 영역은 누르는 즉시 실행되는 페이지라 폼 필드가 없다.
    const saving = new Set<PageKey>(ALL_PAGES.map((p) => p.key).filter((k) => k !== "link" && k !== "danger"));
    for (const k of Object.keys(EMPTY_FORM) as FormKey[]) {
      expect(saving.has(FIELD_META[k].page), k).toBe(true);
    }
  });

  it("저장하는 설정 페이지에는 필드가 하나 이상 있다", () => {
    const used = new Set(Object.values(FIELD_META).map((m) => m.page));
    for (const p of PAGE_GROUPS.flatMap((g) => g.pages)) {
      if (p.key === "link") continue;
      expect(used.has(p.key), p.key).toBe(true);
    }
  });

  it("숫자 필드는 모두 단위가 있다", () => {
    for (const k of Object.keys(NUMBER_SPECS) as NumberField[]) {
      expect(FIELD_META[k].unit, k).toBeTruthy();
    }
  });
});

describe("pageFromParam", () => {
  it.each([
    [null, "general"],
    ["", "general"],
    ["qc", "qc"],
    ["danger", "danger"],
    // 이전 탭 주소와 없어진 페이지 주소는 그 내용을 잇는 페이지로 연다.
    ["ops", "general"],
    ["assign", "general"],
    ["review", "guide"],
    ["analysis", "link"],
    ["nope", "general"],
    // 객체의 기본 속성 이름이 페이지로 읽히지 않는다.
    ["toString", "general"],
    ["constructor", "general"],
  ])("%s → %s", (param, page) => {
    expect(pageFromParam(param)).toBe(page);
  });
});

describe("changeList", () => {
  it("숫자 필드 하나는 단위를 붙여 이전 → 이후로 쓴다", () => {
    const after = { ...BASE, target_hours: "120" };
    expect(changeList(["target_hours"], BASE, after)).toEqual(["목표 분량 100시간 → 120시간"]);
  });

  it("스위치는 사용 여부로 쓴다", () => {
    const after = { ...BASE, deident_enabled: true };
    expect(changeList(["deident_enabled"], BASE, after)).toEqual(["비식별화 사용 안 함 → 사용"]);
  });

  it("상태는 화면에 보이는 이름으로 쓴다", () => {
    const after = { ...BASE, project_status: "paused" };
    expect(changeList(["project_status"], BASE, after)).toEqual(["상태 활성 → 일시정지"]);
  });

  it("비운 숫자는 없음으로 쓴다", () => {
    const after = { ...BASE, target_hours: "" };
    expect(changeList(["target_hours"], BASE, after)).toEqual(["목표 분량 100시간 → 없음"]);
  });

  it("긴 글과 선택 값은 이름만 쓴다", () => {
    const after = { ...BASE, description: "새 설명", qc_adapter_key: "bedrock" };
    expect(changeList(["description", "qc_adapter_key"], BASE, after)).toEqual(["설명", "검사 방식"]);
    expect(changeRows(["qc_adapter_key"], BASE, after)).toEqual([{ label: "검사 방식", values: null }]);
  });
});

describe("patchBody", () => {
  const ALL = new Set(Object.keys(EMPTY_FORM) as FormKey[]);
  const FULL: SettingsForm = {
    ...BASE,
    description: "설명",
    guide_md: "가이드",
    contract_deadline: "2026-12-31",
    deident_enabled: true,
    qc_enabled: true,
    qc_connection_name: " qc-model ",
  };

  it("바뀐 필드만 싣는다", () => {
    expect(patchBody(FULL, new Set<FormKey>())).toEqual({});
    expect(patchBody(FULL, new Set<FormKey>(["name", "target_hours"]))).toEqual({ name: "보도영상 1차", target_hours: 100 });
  });

  it("모든 필드를 고쳐도 계약이 받는 필드만 싣는다", () => {
    const body = patchBody(FULL, ALL);
    const accepted = propsOf("ProjectUpdateRequest");
    for (const k of Object.keys(body)) expect(accepted.has(k), k).toBe(true);
    for (const k of FIXED_FIELDS) expect(body, k).not.toHaveProperty(k);
  });

  it("품질검수는 켜기·검사 방식·모델 연결만 싣는다", () => {
    const body = patchBody(FULL, new Set<FormKey>(["qc_connection_name"]));
    expect(body).toEqual({ caption_qc: { enabled: true, adapter_key: "llm", connection_name: "qc-model" } });
    const accepted = propsOf("CaptionQcInput");
    for (const k of Object.keys(body.caption_qc ?? {})) expect(accepted.has(k), k).toBe(true);
    for (const k of FIXED_QC_FIELDS) expect(body.caption_qc, k).not.toHaveProperty(k);
  });

  it("비운 마감일은 null로 보낸다", () => {
    const body = patchBody({ ...FULL, contract_deadline: "" }, ALL);
    expect(body.contract_deadline).toBeNull();
  });
});

describe("lineItemCount", () => {
  it("빈 줄과 공백 줄은 세지 않는다", () => {
    expect(lineItemCount("")).toBe(0);
    expect(lineItemCount("가\n\n  \n나\n")).toBe(2);
  });
});

describe("changeSummary", () => {
  it("두 항목까지 쓰고 나머지는 건수로 쓴다", () => {
    expect(changeSummary(["이름"])).toBe("이름");
    expect(changeSummary(["이름", "설명"])).toBe("이름 · 설명");
    expect(changeSummary(["이름", "설명", "상태", "모델 연결"])).toBe("이름 · 설명 외 2건");
  });
});
