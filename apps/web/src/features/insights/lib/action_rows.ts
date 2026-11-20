/**
 * 개요 `조치 필요` 카드의 행 판정과 문장, 개요의 카드 배치. 카드 파일과 나눠 두어 화면 없이 테스트한다.
 */

/** 조치 필요 한 행. `count`가 `null`이면 세지 못했다는 뜻이고 0과 다르다. */
export interface ActionRow {
  key: string;
  label: string;
  hint: string;
  count: number | null;
  unit: "건" | "개";
  /** 여는 곳. */
  to: string;
  toLabel: string;
  tone: "bad" | "warn";
}

/**
 * 행 목록을 표에 실을 것과 `없음` 줄로 보낼 것으로 나눈다.
 *
 * 순서는 넘겨받은 순서(우선순위)를 지킨다. 세지 못한 행(`null`)은 표에 남긴다.
 */
export function splitActionRows(rows: ActionRow[]): { shown: ActionRow[]; none: ActionRow[] } {
  return {
    shown: rows.filter((r) => r.count == null || r.count > 0),
    none: rows.filter((r) => r.count === 0),
  };
}

/**
 * 작업 가이드가 없을 때 조치 필요 카드를 오른쪽 열(진척률과 `작업 가이드 없음`)과 나란히 두는 최소 행 수.
 *
 * 오른쪽 열이 진척률 카드와 한 줄짜리 카드뿐이라, 행이 셋이면 조치 필요 카드 높이가 그 열과 비슷해진다.
 */
const SIDE_BY_SIDE_MIN_ROWS = 3;

/**
 * 관리자 개요의 카드 배치.
 *
 * `top`은 조치 필요를 맨 위 전체 폭에 두고 작업 가이드(왼쪽)와 진척률(오른쪽)을 아래 두 열로 둔다.
 * `left`는 조치 필요와 한 줄짜리 `작업 가이드 없음`을 왼쪽 열에, 진척률을 오른쪽 열에 둔다.
 * `side`는 조치 필요를 왼쪽 열에, 진척률과 `작업 가이드 없음`을 오른쪽 열에 둔다.
 */
export type OverviewLayout = "top" | "left" | "side";

/**
 * 관리자 개요의 카드 배치를 정한다. 어느 열의 아래도 크게 비지 않는 배치를 고른다.
 *
 * 조치 필요 카드의 높이는 행 수에 따라 한 줄에서 여섯 행까지 달라진다.
 * 작업 가이드가 있으면 `top`을 쓴다. 조치 필요가 전체 폭이라 행 수와 관계없이 아래 두 열(가이드와 진척률)의 높이가 비슷하다.
 * 작업 가이드가 없으면 가이드 자리가 한 줄이라 `top`의 아래 줄에서 진척률 옆이 빈다.
 * 그래서 행이 적으면 `left`, 많으면 `side`를 쓴다.
 * 불러오는 중과 조회 실패는 카드가 짧아서 행이 적은 경우와 같은 배치를 쓴다.
 *
 * Args:
 *   shownCount: 표에 실린 행 수. 아직 계산하지 못했으면 null이다.
 *   hasGuide: 작업 가이드 본문이 있으면 참.
 */
export function overviewLayout(shownCount: number | null, hasGuide: boolean): OverviewLayout {
  if (hasGuide) return "top";
  return shownCount != null && shownCount >= SIDE_BY_SIDE_MIN_ROWS ? "side" : "left";
}

/**
 * 라벨러·검수자 개요의 카드 배치.
 *
 * `single`은 진척률 없이 이어서 할 작업과 작업 가이드를 한 열로 둔다.
 * `top`은 이어서 할 작업(왼쪽)과 진척률(오른쪽)을 맨 위 한 줄에 두고 작업 가이드를 그 아래 전체 폭에 둔다.
 * `left`는 이어서 할 작업과 한 줄짜리 `작업 가이드 없음`을 왼쪽 열에, 진척률을 오른쪽 열에 둔다.
 */
export type WorkerOverviewLayout = "single" | "top" | "left";

/**
 * 라벨러·검수자 개요의 카드 배치를 정한다. 관리자 개요(`overviewLayout`)와 같은 기준으로 고른다.
 *
 * 작업자 개요의 작업 가이드는 펼친 채 그려서 높이가 한 줄에서 수십 줄까지 달라진다.
 * 가이드를 진척률 옆 열에 두면 가이드가 길수록 진척률 아래가 크게 빈다.
 * 그래서 작업 가이드가 있으면 `top`을 쓴다. 맨 위 줄의 두 카드는 높이가 일정하고 가이드는 전체 폭이라 어느 열도 비지 않는다.
 * 작업 가이드가 없으면 가이드 자리가 한 줄이라 `left`를 쓴다. 왼쪽 열 높이가 진척률 카드와 비슷해진다.
 * 진척률을 보지 않는 역할은 옆 열에 둘 카드가 없어 `single`을 쓴다.
 *
 * Args:
 *   hasProgress: 진척률 카드를 그리면 참.
 *   hasGuide: 작업 가이드 본문이 있으면 참.
 */
export function workerOverviewLayout(hasProgress: boolean, hasGuide: boolean): WorkerOverviewLayout {
  if (!hasProgress) return "single";
  return hasGuide ? "top" : "left";
}

/** `없음` 줄에 이름으로 쓰는 최대 항목 수. 넘으면 앞의 둘만 쓰고 나머지는 수로 줄인다. */
const NONE_NAMES_MAX = 3;

/**
 * 0건 항목 줄의 문장을 만든다. 대상을 먼저 쓰고 `없음`을 끝에 둔다.
 *
 * `없음`이 앞에 오면 뒤의 이름들이 무엇이 없는지 한 번 더 읽어야 풀린다.
 * 이름은 셋까지만 잇는다. 넷부터는 한 줄에 다 읽히지 않아 앞의 둘과 나머지 수로 줄인다.
 * 검사한 항목이 전부 0건이면 이름 없이 `조치 필요 항목 없음` 한 줄로 쓴다.
 *
 * Args:
 *   rows: 0건인 행. 넘겨받은 순서(우선순위)대로 앞의 이름을 쓴다.
 *   total: 검사한 행 수. `rows`와 같으면 전부 0건이다.
 */
export function noneLine(rows: Pick<ActionRow, "label">[], total?: number): string {
  if (total != null && rows.length === total) return "조치 필요 항목 없음";
  if (rows.length <= NONE_NAMES_MAX) return `${rows.map((r) => r.label).join("·")} 없음`;
  const head = rows.slice(0, NONE_NAMES_MAX - 1).map((r) => r.label).join("·");
  return `${head} 외 ${rows.length - (NONE_NAMES_MAX - 1)}개 항목 없음`;
}

/**
 * 납품 불가 사유 분포를 한 줄로 쓴다. 키는 서버가 첫 사유의 항목 이름으로 묶은 값이다.
 *
 * `캡션 92건`만 쓰면 캡션이 92개로 읽혀 `항목`을 붙인다.
 */
export function blockerHint(reasons: Record<string, number>, approved: number | null): string {
  const n = (v: number) => v.toLocaleString("ko-KR");
  return [
    ...(approved != null ? [`승인 ${n(approved)}건 중`] : []),
    ...Object.entries(reasons).map(([k, v]) => `${k} 항목 ${n(v)}건`),
  ].join(" · ");
}

