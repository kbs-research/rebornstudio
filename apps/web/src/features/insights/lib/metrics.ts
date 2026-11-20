/**
 * 지표 사전. 지표마다 이름·정의·계산식·단위·표기 예를 한 곳에 둔다.
 *
 * 같은 지표를 화면마다 다른 이름과 표기로 쓰면 두 화면의 수가 같은 것인지 사람이 확인할 수 없다.
 * 통계·멤버·개요는 이름과 정의를 이 파일에서 가져온다.
 *
 * 계산은 서버가 한다. 이 파일은 이름과 표기만 정한다.
 * 이름은 UI 용어집(제출, 승인율, 반려율, 수정률, 보유 중)을 따른다.
 * 진척률의 이름과 정의는 `lib/progress.ts`에 있다.
 */

/** 지표 하나의 이름과 정의 */
export interface Metric {
  /** 화면 이름. 표 머리·라벨에 그대로 쓴다. */
  readonly name: string;
  /** 뜻을 적는 한 문장. `~입니다.`로 끝난다. */
  readonly meaning: string;
  /** 어느 화면에서나 붙는 설명 문장. 뜻 다음에 온다. */
  readonly notes?: readonly string[];
  /** 계산식. 정의 끝의 괄호 안에 붙는다. */
  readonly formula?: string;
  /** 값의 단위 */
  readonly unit: "건" | "%";
  /** 화면 표기 예. 새 화면은 이 모양을 따른다. */
  readonly example: string;
}

/**
 * 지표 목록.
 *
 * 사람 단위 지표에는 판정 색과 판정어를 붙이지 않는다.
 * 그래서 이 목록에는 임계값이 없다.
 */
export const METRICS = {
  submitted: {
    name: "제출",
    meaning: "라벨링 작업을 제출한 건수입니다.",
    notes: ["반려 후 다시 제출하면 따로 셉니다."],
    unit: "건",
    example: "18건",
  },
  decided: {
    name: "판정",
    meaning: "검수에서 승인하거나 반려한 건수입니다.",
    notes: ["수정 후 승인도 승인으로 셉니다.", "기간 전에 제출한 작업도 기간 안에 판정하면 셉니다."],
    unit: "건",
    example: "24건",
  },
  approvalRate: {
    name: "승인율",
    meaning: "검수 판정 중 승인 판정의 비율입니다.",
    notes: [
      "작업 수가 아니라 검수 판정 수로 셉니다.",
      "수정 후 승인도 승인으로 셉니다.",
      "제출은 있는데 검수 판정이 없으면 측정 전, 제출도 판정도 없으면 —를 씁니다.",
    ],
    formula: "승인 ÷ (승인 + 반려)",
    unit: "%",
    example: "33.3% (2/6)",
  },
  rejectionRate: {
    name: "반려율",
    meaning: "제출한 작업 중 반려된 비율입니다.",
    notes: [
      "아직 검수하지 않은 제출도 계산에 들어가므로 검수가 진행되면 오를 수 있습니다.",
      "기간 전에 제출한 작업이 기간 안에 판정되어 판정이 제출보다 많으면 판정 수로 나눕니다.",
      "승인율과 계산 대상이 달라 둘을 더해도 100%가 되지 않습니다.",
    ],
    formula: "반려 ÷ 제출",
    unit: "%",
    example: "12.5% (2/16)",
  },
  editRate: {
    name: "수정률",
    meaning: "AI 초안의 글자 수 대비 교정한 글자 수의 비율입니다.",
    notes: [
      "작업별 첫 제출만 세고 반려 후 다시 제출한 것은 세지 않습니다.",
      "AI 초안을 보관하기 전의 제출과 초안이 없던 클립은 세지 않습니다.",
      "초안보다 길게 새로 쓰면 100%를 넘을 수 있습니다.",
    ],
    formula: "교정한 글자 ÷ 초안 글자",
    unit: "%",
    example: "12.4% (6건)",
  },
  holding: {
    name: "보유 중",
    meaning: "다음 작업으로 받아 지금 작업 중인 작업 수입니다.",
    notes: [
      "기간과 관계없는 현재 상태입니다.",
      "역할마다 한 건씩 맡습니다. 반려된 재작업이 돌아오면 잠시 두 건일 수 있습니다.",
    ],
    unit: "건",
    example: "보유 1",
  },
} as const satisfies Record<string, Metric>;

/**
 * 정의 문장을 만든다. 뜻, 고정 설명, 화면별 설명, 계산식 순서다.
 *
 * 통계 정의문은 뜻을 먼저 쓰고 계산식은 괄호로 뒤에 붙인다.
 *
 * Args:
 *   m: 지표.
 *   context: 이 화면에만 붙는 설명 문장. 빈 값은 건너뛴다.
 *
 * Returns:
 *   툴팁에 그대로 쓰는 정의 문장.
 */
export function metricDefine(m: Metric, ...context: (string | false | null | undefined)[]): string {
  const sentences = [m.meaning, ...(m.notes ?? []), ...context.filter((s): s is string => !!s)];
  const body = sentences.join(" ");
  return m.formula ? `${body} (${m.formula})` : body;
}

/** 분자·분모 괄호(`(2/6)`)를 쓴다. 비율 아래 줄이나 뒤에 붙는다. */
export function fmtFraction(num: number, den: number): string {
  return `(${num.toLocaleString("ko-KR")}/${den.toLocaleString("ko-KR")})`;
}
