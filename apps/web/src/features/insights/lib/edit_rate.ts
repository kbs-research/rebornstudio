/**
 * 수정률 표시 값을 고르는 규칙. 서버가 준 수를 읽기만 한다.
 *
 * 비율과 분모는 서버(`analytics.dashboard._edit_rate`)가 계산한다.
 * 건별 비율의 평균 대신 글자 수 합계의 비율이다.
 * 무엇을 재는지(작업별 첫 제출·정규화·편집 거리)도 서버 한 곳
 * (`services/work/edit_rate.py`)이 정한다.
 * 이 파일은 세 가지 중 무엇을 그릴지만 고르고 나눗셈은 하지 않는다
 * (판정 규칙은 한 곳에만 둔다).
 *
 * 측정한 제출이 있으면 `12.4%`와 그 비율의 분자·분모(교정한 글자/초안 글자)를 보여 준다.
 * 다른 비율 열의 `(28/37)`과 같은 꼴이라, 초안보다 길게 고쳐 100%를 넘은 값도 두 수로 확인할 수 있다.
 * 제출은 있는데 측정한 것이 없으면 `측정 전`이다. AI 초안을 보관하기 전의 제출이거나
 * 초안이 없던 클립이다. `0%`로 그리면 "하나도 고치지 않았다"로 읽힌다.
 * 제출이 없으면 `—`다. 옆 비율 열들과 같은 빈 값 표시다.
 *
 * 사람 표와 사람 서랍이 함께 쓴다.
 * 두 곳이 각자 고르면 같은 사람이 화면마다 다르게 보인다.
 */
import { metricDefine, METRICS } from "./metrics";

/** 측정한 제출이 없을 때의 값 라벨. 용어 표준의 `측정 전`이다(값 라벨은 단어로 쓴다). */
export const EDIT_RATE_UNMEASURED = "측정 전";

/** 수정률 표시 하나가 그릴 값. `rate`일 때만 측정 건수가 있다. */
export type EditRateView =
  | { kind: "rate"; text: string; measured: number }
  | { kind: "unmeasured"; text: string }
  | { kind: "none"; text: string };

/**
 * 사람 한 명의 수정률 표시 값을 고른다.
 * `LabelerPerfRow`가 이 모양이다.
 */
export function editRateView(r: {
  submitted: number;
  edit_rate?: number | null;
  edit_measured?: number;
}): EditRateView {
  if (r.edit_rate != null) {
    return { kind: "rate", text: `${r.edit_rate}%`, measured: r.edit_measured ?? 0 };
  }
  if (r.submitted > 0) return { kind: "unmeasured", text: EDIT_RATE_UNMEASURED };
  return { kind: "none", text: "—" };
}

/**
 * 수정률의 정의 문구. 표 머리·드로어 라벨의 점선 밑줄 툴팁이 보여 준다.
 * `period`는 기간 이름(`최근 7일` 등)이다.
 *
 * 뜻과 계산식은 지표 사전(`lib/metrics`)에서 가져온다. 뜻을 먼저 쓰고 계산식은 괄호로 뒤에 붙인다.
 * 분모가 초안 글자 수라 값이 100%를 넘을 수 있다. 정의를 읽는 사람이 그 값을 오류로 보지 않게 정의에 함께 쓴다.
 */
export function editRateDefine(period: string): string {
  return metricDefine(
    METRICS.editRate,
    `${period} 안의 제출을 셉니다.`,
    "추가·삭제·교체한 글자를 한 글자씩 셉니다.",
    "초안보다 많이 고치면 100%를 넘습니다.",
    "괄호 안은 교정한 글자와 AI 초안 글자이고, 측정한 제출이 없으면 측정 전입니다.",
  );
}

/** 글자 수의 분자와 분모. 서버가 준 합계를 그대로 쓴다. */
interface EditChars {
  edit_distance?: number;
  draft_chars?: number;
  edit_measured?: number;
  edit_rate?: number | null;
}

/** 수정률의 분자와 분모를 `(교정/초안자)` 꼴로 쓴다. 다른 비율 열의 `(28/37)`과 같은 꼴이다. */
export function editRateFraction(r: EditChars): string {
  const n = (v: number | undefined) => (v ?? 0).toLocaleString("ko-KR");
  return `(${n(r.edit_distance)}/${n(r.draft_chars)}자)`;
}

/**
 * 수정률 값에 붙이는 설명. 분자·분모와 측정한 제출 수를 쓰고, 100%를 넘으면 그 이유를 덧붙인다.
 * 표의 괄호만으로는 어느 수가 분자인지 읽을 수 없어 값의 점선 밑줄에서 풀어 쓴다.
 */
export function editRateNote(r: EditChars): string {
  const n = (v: number | undefined) => (v ?? 0).toLocaleString("ko-KR");
  const base =
    `교정한 글자 ${n(r.edit_distance)}자를 AI 초안 글자 ${n(r.draft_chars)}자로 나눈 값입니다. ` +
    `측정한 제출은 ${n(r.edit_measured)}건입니다.`;
  return editRateOver(r.edit_rate) ? `${base} ${EDIT_RATE_OVER_NOTE}` : base;
}

/**
 * 수정률이 100%를 넘을 때 값에 붙이는 설명.
 *
 * 정의(교정한 글자 ÷ 초안 글자)상 초안보다 길게 고쳐 쓰면 100%를 넘는다. 값은 자르지 않는다.
 * 설명이 없으면 `718.6%` 같은 값이 계산 오류로 읽히므로 그 값에만 점선 밑줄과 이 설명을 붙인다.
 */
export const EDIT_RATE_OVER_NOTE =
  "교정한 글자가 AI 초안 글자보다 많아 100%를 넘습니다. 초안보다 길게 고쳐 쓴 제출이 있다는 뜻이며 계산 오류가 아닙니다.";

/** 수정률 값이 100%를 넘으면 true. 서버가 준 값만 읽는다. */
export function editRateOver(rate: number | null | undefined): boolean {
  return rate != null && rate > 100;
}
