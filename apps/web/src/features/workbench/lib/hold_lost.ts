/**
 * 화면을 보는 사이 보유가 끝난 라벨링 작업에 표시할 문구와 판정. 라벨링 워크벤치가 저장·제출 실패와 다시 받은 작업에 쓴다.
 *
 * 서버는 대기열로 돌아간 작업에 늦게 저장하면 `work_item_is_available`을, 보유하지 않은 작업을 열면 `not_your_claim`을 돌려준다.
 * 보유에는 끝나는 시각이 없다. 보유가 풀리는 경우는 다른 탭의 비식별화 신고와 프로젝트 나가기·멤버 제거다.
 * 화면에서는 두 경우를 구분할 수 없으므로 어느 경우에나 맞는 문구를 쓰고, 편집이 저장되지 않았다는 사실을 함께 알린다.
 * 렌더 없이 테스트하려고 컴포넌트 밖에 둔다.
 */
import type { ErrorText } from "@/shared/api/error_text";

/** 보유가 끝났을 때 화면에 그리는 문구. 결과와 이유를 줄로 나눈다. */
export const HOLD_LOST: ErrorText = {
  message: "보유가 끝난 작업입니다",
  hint: "신고나 멤버 변경으로 보유가 풀렸습니다.",
};

/** 보유가 끝나 저장·제출이 거절됐을 때의 토스트 문구. 화면 문구에 편집이 저장되지 않았다는 사실을 더한다. */
export const HOLD_LOST_SAVE: ErrorText = {
  message: HOLD_LOST.message,
  hint: `${HOLD_LOST.hint} 이 화면의 편집은 저장되지 않았습니다.`,
};

/** 저장·제출 실패 코드가 보유가 끝났다는 뜻이면 true. */
export function isHoldLost(detail: unknown): boolean {
  return detail === "not_your_claim" || detail === "work_item_is_available";
}

/**
 * 다시 받은 작업이 403이면, 현재 사용자가 그 작업을 보유하고 있었는지로 보유가 끝났는지 정한다.
 *
 * Args:
 *   status: 다시 받기 실패의 HTTP 상태.
 *   lastClaimedBy: 실패 전 마지막으로 받은 작업의 보유자.
 *   myUserId: 현재 사용자.
 *
 * Returns:
 *   현재 사용자가 보유하던 작업이 403으로 막혔으면 true. 처음부터 다른 사람의 작업이었으면 false(접근 권한 없음).
 */
export function lostWhileOpen(
  status: number | undefined,
  lastClaimedBy: string | null | undefined,
  myUserId: string | null | undefined,
): boolean {
  return status === 403 && !!myUserId && lastClaimedBy === myUserId;
}
