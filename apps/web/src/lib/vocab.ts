/**
 * 판정 값의 화면 이름. 클립 상태와 검수 판정을 여러 화면이 같은 이름으로 부르게 한다.
 *
 * 사전이 화면마다 있으면 그중 하나만 고쳐지고 나머지는 이전 이름으로 남는다
 * (`features/ingest/lib/vocab.ts`가 준비 단계에서 같은 일을 한다).
 *
 * 기준은 화면 용어 표준이다. 값은 서버 enum 그대로 두고 이름만 바꾼다.
 * 사전에 없는 값은 부르는 쪽이 `?? 원문`으로 받는다. 감추면 새 값이 표시 없이 사라진다.
 */

/**
 * 클립 상태(`rights_status`)의 이름. RebornStudio의 클립 상태는 셋이다.
 * 분석으로 만든 클립은 `auto_cleared`라 `정상`이다. `cleared`·`cleared_with_edit`는 RebornStudio Pro에서
 * 가져온 데이터에만 있다. 같은 뜻이라 같은 이름으로 부른다.
 * `legal_review`는 비식별화 미흡 신고로 작업이 멈춘 상태다.
 * `rejected`는 신고를 처리하며 데이터셋에서 뺀 상태라 검수 `반려`와 구분해 `제외`로 부른다.
 */
export const RIGHTS_LABEL: Record<string, string> = {
  auto_cleared: "정상",
  cleared: "정상",
  cleared_with_edit: "정상",
  rejected: "제외",
  legal_review: "비식별화 신고",
};

/** 검수 판정 값의 이름. */
export const VERDICT_LABEL: Record<string, string> = {
  approve: "승인",
  reject: "반려",
  modify_and_approve: "수정 후 승인",
};
