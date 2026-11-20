/**
 * 승인율·반려율 칸에 그릴 값을 고르는 규칙. 서버가 준 수를 읽기만 한다.
 *
 * 비율과 그 비율을 낸 분모는 서버(`analytics.dashboard._rate_fields`)가 정해 행에 싣는다
 * (`approval_base`·`rejection_base`). 이 모듈은 나누지 않고 분모를 다시 계산하지도 않는다.
 * 화면이 제출 수로 괄호를 쓰면 판정 수가 제출 수보다 큰 행에서 `100% (18/0)`처럼
 * 비율과 괄호가 다른 수를 가리킨다.
 * 기대값은 판정 표 `packages/reborn-contracts/labeler-rates.json`에 있고 서버 테스트도 같은 표를 읽는다.
 *
 * 서버 비율의 단위는 두 가지다. `approval_rate`는 0~100이고 `rejection_rate`는 0~1이다.
 * 다른 화면이 그 단위를 전제로 하므로 서버 단위는 그대로 두고 화면이 한 함수로 바꾼다.
 * 사람 표와 서랍이 각자 `Math.round(r * 100)`을 쓰면 자릿수가 달라지고, 같은 값이 화면마다 다르게 나간다.
 */
import { fmtFraction } from "./metrics";

/** 판정이 없는 승인율 칸의 값 라벨. 수정률의 `측정 전`과 같은 말이다. */
export const RATE_UNMEASURED = "측정 전";

/**
 * 비율 칸 하나가 그릴 값.
 *
 * `rate`면 `sub`는 분자·분모 괄호다. 나머지는 값이 없는 이유이고, 사람 서랍이 값 아래에 쓴다.
 */
export interface RateView {
  kind: "rate" | "unmeasured" | "none";
  text: string;
  sub: string;
}

/** 승인율 칸이 읽는 행의 수. `LabelerPerfRow`가 이 모양이다. */
interface ApprovalRow {
  submitted: number;
  approved: number;
  approval_rate?: number | null;
  approval_base: number;
}

/** 반려율 칸이 읽는 행의 수. `LabelerPerfRow`가 이 모양이다. */
interface RejectionRow {
  rejected: number;
  rejection_rate?: number | null;
  rejection_base: number;
}

/**
 * 반려율(0~1)을 소수 1자리 퍼센트로 바꾼다.
 *
 * 값이 없으면 0% 대신 `null`을 돌려준다. 서버는 분모가 0이면 `null`을 준다.
 */
export function rejectionPct(rate: number | null | undefined): number | null {
  return rate == null ? null : Math.round(rate * 1000) / 10;
}

/**
 * 승인율 칸의 값을 고른다.
 *
 * 판정이 있으면 서버 비율과 `(승인/판정 수)`를 쓴다.
 * 제출은 있는데 판정이 없으면 `측정 전`이다. `—`로 그리면 제출이 없는 사람과 구별되지 않는다.
 * 제출도 판정도 없으면 `—`다.
 */
export function approvalView(r: ApprovalRow): RateView {
  if (r.approval_rate != null) {
    return { kind: "rate", text: `${r.approval_rate}%`, sub: fmtFraction(r.approved, r.approval_base) };
  }
  if (r.submitted > 0) return { kind: "unmeasured", text: RATE_UNMEASURED, sub: "판정 없음" };
  return { kind: "none", text: "—", sub: "판정 없음" };
}

/**
 * 반려율 칸의 값을 고른다.
 *
 * 반려율은 제출이 있으면 판정 전에도 값이 있다(`0% (0/5)`). 괄호는 서버가 나눈 분모를 그대로 쓴다.
 * 제출도 판정도 없으면 서버가 `null`을 주고 칸은 `—`다.
 */
export function rejectionView(r: RejectionRow): RateView {
  const pct = rejectionPct(r.rejection_rate);
  if (pct == null) return { kind: "none", text: "—", sub: "제출 없음" };
  return { kind: "rate", text: `${pct}%`, sub: fmtFraction(r.rejected, r.rejection_base) };
}

/**
 * 표시 값 끝의 `%`를 떼어 값과 단위로 나눈다. `87.5%`는 [`87.5`, `%`]가 되고, `측정 전`·`—`는 그대로 둔다.
 *
 * 사람 서랍과 통계 요약 칸은 큰 수 옆에 단위를 작게 쓴다. 두 곳이 각자 자르면 한쪽만 `1,234.5%` 같은 값을 놓친다.
 */
export function splitUnit(text: string): [string, string | undefined] {
  const m = /^([\d.,]+)(%)$/.exec(text);
  return m ? [m[1], m[2]] : [text, undefined];
}
