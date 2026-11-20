/**
 * 프로젝트 설정 폼의 필드 표. 필드마다 어느 설정 페이지에 있는지와 이름·단위·설명을 둔다.
 *
 * 설정 화면(`pages/project_settings.tsx`)은 이 표로 메뉴의 변경 표시와 저장 막대의 변경 목록을 만든다.
 * 폼 필드가 이 표에 없으면 타입 검사가 실패한다. 폼에 필드를 더하면 이 표에도 적는다.
 * 숫자 필드의 범위와 정수 여부는 `settings_numbers.ts`의 `NUMBER_SPECS`에 있다.
 *
 * 처리 옵션(캡션 최소 길이·반려 사유·클립 길이·장면 요약 다듬기·문장별 시각 표시·
 * 원본 영상 보관·비식별화 목표 비율·품질검수 사전과 프롬프트)은 모든 프로젝트가 같은 기본값을 쓴다.
 * 서버는 그 필드를 받으면 422로 거절하므로 이 폼에는 그 필드가 없다.
 * 동시 보유 한도와 보유 시간은 없다. 한 사람은 역할마다 한 건씩 맡고, 보유는 제출·판정·신고로 끝난다.
 */
import type { ProjectUpdateRequest } from "../api";
import { PROJECT_STATUS_LABEL, type ProjectStatus } from "./project_status";

/**
 * 폼의 처음 값. 서버 값을 받기 전까지 쓴다.
 *
 * 값은 모두 문자열이나 불리언이다. 그래야 `form[k] !== base[k]`로 변경 여부를 판단할 수 있고,
 * 켰다가 다시 끄면 변경 없음으로 돌아간다.
 */
export const EMPTY_FORM = {
  name: "", description: "", guide_md: "", target_hours: "", contract_deadline: "", project_status: "active",
  deident_enabled: false,
  // 캡션 품질검수. 서버가 DTO의 `caption_qc`로 준다.
  qc_enabled: false, qc_adapter_key: "llm", qc_connection_name: "",
};

export type SettingsForm = typeof EMPTY_FORM;
export type FormKey = keyof SettingsForm;

/** 설정 페이지 키. 주소의 `?tab=` 값이다. */
export type PageKey =
  | "general" | "guide" | "qc" | "deident" | "link" | "danger";

/** 왼쪽 메뉴의 항목 하나. */
export interface SettingsPage {
  key: PageKey;
  label: string;
}

/** 왼쪽 메뉴의 묶음과 순서. 위험 영역은 묶음 밖에 따로 둔다(`DANGER_PAGE`). */
export const PAGE_GROUPS: { label: string; pages: SettingsPage[] }[] = [
  {
    label: "프로젝트",
    pages: [{ key: "general", label: "기본 정보" }, { key: "guide", label: "작업 가이드" }],
  },
  {
    label: "작업 운영",
    pages: [
      { key: "qc", label: "캡션 품질검수" },
    ],
  },
  {
    label: "영상 처리",
    pages: [
      { key: "deident", label: "비식별화" },
      { key: "link", label: "분석 연동" },
    ],
  },
];

export const DANGER_PAGE: SettingsPage = { key: "danger", label: "위험 영역" };
export const ALL_PAGES: SettingsPage[] = [...PAGE_GROUPS.flatMap((g) => g.pages), DANGER_PAGE];

/**
 * 없어진 페이지 이름별로 열 페이지. 공유된 링크가 빈 페이지나 엉뚱한 첫 페이지로 열리지 않게 한다.
 * `ops`(작업 운영)와 `assign`(작업 배분)은 남은 설정이 없어 기본 정보를 연다.
 * `review`(검수 기준)는 캡션 최소 길이와 반려 사유를 안내하는 작업 가이드를, `analysis`(분석)는 남은 분석 설정인 분석 연동을 연다.
 */
const LEGACY_PAGE = new Map<string, PageKey>([
  ["ops", "general"], ["assign", "general"], ["review", "guide"], ["analysis", "link"],
]);

/** 주소의 `?tab=` 값을 페이지 키로 바꾼다. 표에 없는 값이면 기본 정보를 연다. */
export function pageFromParam(value: string | null): PageKey {
  if (!value) return "general";
  return ALL_PAGES.find((p) => p.key === value)?.key ?? LEGACY_PAGE.get(value) ?? "general";
}

/** 필드 하나의 위치와 표시 정보. */
export interface FieldMeta {
  /** 이 필드가 있는 설정 페이지. */
  page: PageKey;
  /** 설정 이름. 저장 막대의 변경 목록에도 쓴다. */
  label: string;
  /** 숫자 입력란 안에 붙는 단위. */
  unit?: string;
  /** 설정 행의 설명. 이 값이 무엇을 바꾸는지 쓴다. */
  hint?: string;
}

export const FIELD_META: Record<FormKey, FieldMeta> = {
  name: { page: "general", label: "이름" },
  description: { page: "general", label: "설명" },
  project_status: { page: "general", label: "상태" },
  contract_deadline: { page: "general", label: "계약 마감일" },
  target_hours: {
    page: "general",
    label: "목표 분량",
    unit: "시간",
    // 통계 진척에 쓰인다는 사실은 계약 마감일과 함께 묶음 머리에 한 번 쓴다.
    hint: "처리할 영상의 총 길이입니다.",
  },
  guide_md: { page: "guide", label: "작업 가이드" },

  qc_enabled: { page: "qc", label: "캡션 품질검수" },
  qc_adapter_key: { page: "qc", label: "검사 방식" },
  qc_connection_name: { page: "qc", label: "모델 연결" },

  deident_enabled: { page: "deident", label: "비식별화" },
};

/** 여러 줄 입력(작업 가이드)의 줄 수. 빈 줄은 세지 않는다. */
export function lineItemCount(text: string): number {
  return text.split("\n").filter((line) => line.trim()).length;
}

/** 변경 목록에 쓸 짧은 값. 짧게 쓸 수 없는 값(긴 글, 검사 방식과 모델 연결)은 `null`이다. */
function valueText(key: FormKey, value: string | boolean): string | null {
  if (typeof value === "boolean") return value ? "사용" : "사용 안 함";
  if (key === "project_status") return PROJECT_STATUS_LABEL[value as ProjectStatus] ?? value;
  if (key === "contract_deadline") return value || "없음";
  const unit = FIELD_META[key].unit;
  if (unit !== undefined) return value === "" ? "없음" : `${value}${unit}`;
  return null;
}

/** 변경 한 건. 값을 짧게 쓸 수 없으면 `values`가 `null`이다. */
export interface ChangeRow {
  label: string;
  values: { from: string; to: string } | null;
}

/**
 * 바뀐 필드를 변경 항목으로 바꾼다. 값을 짧게 쓸 수 있으면 이전 값과 이후 값을 함께 둔다.
 * 나가기 확인 창은 이 항목을 표로, 저장 막대는 `changeList`로 한 줄에 쓴다.
 *
 * Args:
 *   keys: 바뀐 필드. 폼 순서대로 준다.
 *   before: 서버 값.
 *   after: 지금 폼 값.
 */
export function changeRows(keys: readonly FormKey[], before: SettingsForm, after: SettingsForm): ChangeRow[] {
  return keys.map((k) => {
    const from = valueText(k, before[k]);
    const to = valueText(k, after[k]);
    // 짧은 값이 같으면 값을 쓰지 않는다. 같은 글자 둘로는 무엇이 바뀌었는지 알 수 없다.
    return { label: FIELD_META[k].label, values: from != null && to != null && from !== to ? { from, to } : null };
  });
}

/** 변경 항목을 `이름 이전 → 이후` 문자열로 쓴다. 값이 없는 항목은 이름만 쓴다. */
export function changeList(keys: readonly FormKey[], before: SettingsForm, after: SettingsForm): string[] {
  return changeRows(keys, before, after).map((r) => (r.values ? `${r.label} ${r.values.from} → ${r.values.to}` : r.label));
}

/** 변경 목록을 한 줄로 줄인다. 앞의 두 항목을 쓰고 나머지는 건수로 쓴다. */
export function changeSummary(items: readonly string[]): string {
  const head = items.slice(0, 2).join(" · ");
  return items.length > 2 ? `${head} 외 ${items.length - 2}건` : head;
}

/**
 * 바뀐 필드만 담은 저장(PATCH) 본문을 만든다.
 * 캡션 품질검수는 필드 하나만 바뀌어도 켜기·검사 방식·모델 연결을 함께 보낸다.
 * 반환 타입이 계약의 요청 모델이라 서버가 받지 않는 필드를 넣으면 타입 검사가 실패한다.
 *
 * Args:
 *   form: 지금 폼 값.
 *   dirty: 서버 값과 다른 필드.
 *
 * Returns:
 *   `PATCH /projects/{id}` 본문. 바뀐 필드가 없으면 빈 객체다.
 */
export function patchBody(form: SettingsForm, dirty: ReadonlySet<FormKey>): ProjectUpdateRequest {
  const num = (v: string) => (v ? Number(v) : null);
  const body: ProjectUpdateRequest = {};
  if (dirty.has("name")) body.name = form.name;
  if (dirty.has("description")) body.description = form.description;
  if (dirty.has("guide_md")) body.guide_md = form.guide_md;
  if (dirty.has("target_hours")) body.target_hours = num(form.target_hours);
  if (dirty.has("contract_deadline")) body.contract_deadline = form.contract_deadline || null;
  if (dirty.has("project_status")) body.project_status = form.project_status as ProjectStatus;
  if (dirty.has("deident_enabled")) body.deident_enabled = form.deident_enabled;
  if (dirty.has("qc_enabled") || dirty.has("qc_adapter_key") || dirty.has("qc_connection_name")) {
    body.caption_qc = {
      enabled: form.qc_enabled,
      adapter_key: form.qc_adapter_key,
      connection_name: form.qc_connection_name.trim() || null,
    };
  }
  return body;
}
