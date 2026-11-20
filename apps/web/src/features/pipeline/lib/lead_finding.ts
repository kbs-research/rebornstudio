/**
 * 품질검수 보류의 대표 지적을 고른다. 보류 표의 `지적` 열과 다시 검사 확인 창이 쓴다.
 *
 * 지적은 검사 단계 순서로 온다. 첫 지적을 대표로 고르면 판정을 만든 오류 대신 앞 단계의 경고가
 * 대표가 되어 `반려 추천` 행이 `클립 길이 외 1건`으로 보인다.
 * 무게는 판정 규칙과 같은 순서(오류, 경고, 참고 순, 서버 `rules.verdict_of`)다. 같은 무게면 단계 순서를 따른다.
 */

/** 지적 하나에서 대표를 고르는 데 쓰는 값. */
type Finding = { severity?: string | null };

/** 심각도의 무게. 클수록 판정에 크게 반영된다. 표에 없는 값은 참고와 같게 본다. */
const WEIGHT: Record<string, number> = { error: 2, warn: 1, info: 0 };

/** 가장 무거운 지적을 돌려준다. 지적이 없으면 `undefined`다. */
export function leadFinding<T extends Finding>(findings: readonly T[] | null | undefined): T | undefined {
  let lead: T | undefined;
  for (const f of findings ?? []) {
    if (lead === undefined || (WEIGHT[f.severity ?? ""] ?? 0) > (WEIGHT[lead.severity ?? ""] ?? 0)) lead = f;
  }
  return lead;
}
