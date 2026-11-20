/**
 * 완료 목록의 거르기 축. 축의 값과 이름을 이 파일 한 곳에서 정한다.
 *
 * 완료 목록(`DonePanel`)과 내보내기 패널의 요약 줄(`ExportPanel`)이 함께 쓴다. 요약 줄의 `납품 불가 4건`을 누르면 그 축이 열린다.
 * 두 곳이 각자 문자열을 적으면 한쪽만 고쳤을 때 링크가 알림 없이 `전체`로 열린다.
 * 주소에 오타가 나도 화면은 정상으로 보여 눈에 띄지 않는다.
 *
 * 값은 서버 `?filter=`와 같은 낱말이다(계약 `CompletedFilter`). 화면이 이름을 따로 매핑하지 않는다.
 * 이름은 화면이 이미 쓰는 말을 쓴다. 납품 열은 `대상`·`불가`, 품질검수 열은 `규칙만`이다.
 * `제외`는 클립 상태(비식별화 신고로 뺀 클립)를 가리키므로 납품 불가는 `불가`라고 부른다.
 */

/** 서버 `?filter=` 값. 계약 `CompletedFilter`와 같다. */
export type CompletedFilter = "all" | "deliverable" | "excluded" | "qc_rules";

/** 축 네 개. 배열 순서는 세그먼트가 그리는 순서이고, `value`는 주소에 쓰는 값이다. */
export const COMPLETED_FILTERS: { value: CompletedFilter; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "deliverable", label: "납품 대상" },
  { value: "excluded", label: "납품 불가" },
  { value: "qc_rules", label: "규칙만 검사" },
];

/**
 * `규칙만 검사`의 뜻. 축 이름과 품질검수 열의 `규칙만` 칩에 툴팁으로 붙인다.
 * 이름만으로는 무엇이 빠졌는지(AI 모델 검사) 읽히지 않고, 납품에서 빠지는지도 알 수 없다.
 */
export const QC_RULES_NOTE =
  "캡션 품질검수에서 AI 모델을 부르지 못해 규칙 검사만 거친 승인 클립입니다. 보류되지 않았으므로 납품에 함께 실립니다.";

/**
 * 주소의 `?filter=` 값을 축으로 읽는다. 사전에 없는 값은 `all`로 읽는다.
 *
 * 북마크나 손으로 고친 주소의 값을 그대로 서버에 보내면 422 응답을 받는다.
 * 축을 고르는 컨트롤이 화면에 있으므로 목록 화면을 오류로 바꾸지 않고 전체 목록을 보여 준다.
 */
export function completedFilterOf(raw: string | null): CompletedFilter {
  return COMPLETED_FILTERS.some((f) => f.value === raw) ? (raw as CompletedFilter) : "all";
}

/** 축 이름을 돌려준다. 표 아래 전체 수 표기와 빈 상태가 이 이름을 함께 쓴다. */
export function completedFilterLabel(f: CompletedFilter): string {
  return COMPLETED_FILTERS.find((x) => x.value === f)?.label ?? "전체";
}

/**
 * 프로젝트에서 보이는 축을 순서대로 돌려준다. 세그먼트와 주소 정리가 같은 결과를 쓴다.
 *
 * 품질검수를 쓰지 않는 프로젝트에는 `규칙만 검사` 축이 없다. 칩만 남기면 항상 0인 축이 된다.
 * 다만 켜 있던 동안 규칙만 검사한 완료분이 남았으면 꺼도 축을 둔다. 0이 아닌 수를 숨기면 그 건을 찾을 수 없다.
 */
export function visibleCompletedFilters({ qcEnabled, qcRulesCount }: {
  qcEnabled: boolean;
  qcRulesCount: number | null | undefined;
}): CompletedFilter[] {
  return COMPLETED_FILTERS.map((f) => f.value).filter((v) => {
    if (v === "qc_rules") return qcEnabled || (qcRulesCount ?? 0) > 0;
    return true;
  });
}
