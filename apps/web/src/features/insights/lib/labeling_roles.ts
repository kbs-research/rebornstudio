/**
 * 사람별 라벨링 표에서 누가 라벨링하는 사람인지 정하는 규칙.
 *
 * 역할 목록은 화면에서 라벨링 대기열을 여는 역할이다. `내 작업`의 라벨링 대기열은 라벨러와 관리자가 연다.
 * 서버(`work.service._ANNOTATOR_ROLES`)는 검수자에게도 라벨링을 허용하지만 화면은 검수자에게 검수 대기열을 준다.
 * 역할과 관계없이 기간 안에 제출했거나 판정받은 사람은 라벨링하는 사람으로 본다. 멤버가 아닌 사람도 그렇다.
 * 기간 전에 낸 작업이 기간 안에 판정된 사람을 숨기면 표의 판정 합이 위 요약 칸의 판정 수보다 작아진다.
 * 활성 멤버 전원을 실으면 검수자처럼 구조적으로 0인 행이 실제 라벨러 사이에 섞이고,
 * 그 행을 누르면 빈 라벨러 상세가 열린다.
 */

/** 화면에서 라벨링 대기열을 여는 프로젝트 역할 */
export const LABELING_ROLES: readonly string[] = ["annotator", "project_admin"];

/** 라벨링하는 사람으로 보는 데 필요한 행의 수. `approval_base`는 기간 안의 판정 수이고 없으면 0으로 본다. */
interface LabelingRow {
  role?: string | null;
  submitted: number;
  approval_base?: number;
}

/** 역할이 라벨링 역할이거나 기간 안에 제출했거나 판정받은 기록이 있으면 true. */
export function doesLabeling(row: LabelingRow): boolean {
  return (
    row.submitted > 0 ||
    (row.approval_base ?? 0) > 0 ||
    (row.role != null && LABELING_ROLES.includes(row.role))
  );
}

/**
 * 표에 실을 행과 라벨링하지 않는 멤버 수를 정한다.
 *
 * Args:
 *   rows: 서버가 준 사람별 행.
 *   showAll: 라벨링하지 않는 멤버까지 모두 보이려면 true.
 *
 * Returns:
 *   실을 행과 라벨링하지 않는 멤버 수. 그 수는 `showAll`과 관계없이 센다(토글 문구에 쓴다).
 */
export function labelingRows<T extends LabelingRow>(
  rows: T[],
  showAll: boolean,
): { shown: T[]; others: number } {
  const labeling = rows.filter(doesLabeling);
  return { shown: showAll ? rows : labeling, others: rows.length - labeling.length };
}
