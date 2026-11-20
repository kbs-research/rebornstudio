/**
 * 멤버십 상태의 이름. 멤버 표·서랍·머리 요약이 같은 사전을 쓴다.
 *
 * 멤버십 상태에는 멤버십 전용 이름을 쓴다. `StatusBadge`의 공용 표를 빌려 쓰면 나간(`left`) 행은
 * 빈 칩이 되고, 거절된(`rejected`) 행은 검수 용어인 `반려`로 나온다.
 * 승인 대기와 거절은 참여 요청이 있던 데이터에만 남는 상태다.
 */
export const MEMBER_STATUS_LABEL: Record<string, string> = {
  active: "활성",
  // 멤버십 승인을 기다리는 상태다. `대기`만 쓰면 이 앱에서 대기열의 뜻으로 읽힌다.
  pending: "승인 대기",
  left: "나감",
  rejected: "거절",
  suspended: "정지",
};

/**
 * 멤버십 상태를 이름으로 바꾼다.
 * 사전에 없는 값은 원문 대신 `—`로 표시한다(DB 제약상 생기지 않는다).
 */
export function memberStatusLabel(status: string): string {
  return MEMBER_STATUS_LABEL[status] ?? "—";
}
